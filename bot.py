import os
import discord
from discord.ext import commands, tasks
from discord import app_commands
from dotenv import load_dotenv
import yt_dlp
from collections import deque
import asyncio
import logging

# Configuração de logging para melhor depuração
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger('discord_music_bot')

# Variáveis de ambiente para tokens e outros dados sensíveis
load_dotenv()
TOKEN = os.getenv("TOKEN") 

# Estrutura para enfileirar músicas: Dicionário de filas (deque) por ID da guilda
SONG_QUEUES = {}
# Dicionário para armazenar os temporizadores de inatividade por ID da guilda
INACTIVITY_TIMERS = {}

# Configuração das intents. Intents são permissões que o bot tem no servidor
intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True # Essencial para interagir com canais de voz

# Configuração do bot
bot = commands.Bot(command_prefix="!", intents=intents) # Mantendo '!' como prefixo para comandos de texto

# Opções padrão do yt-dlp para extração de áudio
# Ajustado para um bitrate mais baixo para otimização e para tentar evitar detecção de bot do YouTube
YTDL_OPTIONS = {
    "format": "bestaudio[abr<=96]/bestaudio", 
    "noplaylist": True,
    "youtube_include_dash_manifest": False,
    "youtube_include_hls_manifest": False,
    "quiet": True, # Suprime a saída padrão do yt-dlp
    "no_warnings": True, # Suprime avisos do yt-dlp
    "geo_bypass": True, # Tenta contornar restrições geográficas
    "referer": "https://www.youtube.com/", # Define um referer para a requisição
    "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36", # Simula um user-agent de navegador
    "extract_flat": True, # Não extrai informações de playlists, apenas URLs
}

# Opções do FFmpeg para processamento de áudio
FFMPEG_OPTIONS = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    "options": "-vn -c:a libopus -b:a 96k",
    # Certifique-se de que o caminho para o FFmpeg está correto no ambiente do Railway
    "executable": "bin/ffmpeg/ffmpeg" # Caminho para Linux, ajuste se necessário
}

# --- Funções Auxiliares ---

async def search_ytdlp_async(query, ydl_opts):
    """Executa a busca do yt-dlp em um executor de thread para não bloquear o loop de eventos."""
    loop = asyncio.get_running_loop()
    try:
        # Tenta extrair informações. Se for um URL, tenta extrair diretamente.
        # Se for uma query de busca, adiciona 'ytsearch1:'
        if "http" in query:
            info = await loop.run_in_executor(None, lambda: yt_dlp.YoutubeDL(ydl_opts).extract_info(query, download=False))
        else:
            info = await loop.run_in_executor(None, lambda: yt_dlp.YoutubeDL(ydl_opts).extract_info(f"ytsearch1:{query}", download=False))
        return info
    except Exception as e:
        logger.error(f"Erro ao buscar com yt-dlp para '{query}': {e}")
        return None

async def play_next_song(voice_client, guild_id, channel):
    """Reproduz a próxima música na fila ou desconecta se a fila estiver vazia."""
    # Para garantir que o bot não tente reproduzir se já estiver desconectado
    if not voice_client or not voice_client.is_connected():
        logger.info(f"Bot desconectado da guilda {guild_id}. Limpando fila.")
        if guild_id in SONG_QUEUES:
            SONG_QUEUES[guild_id].clear()
        return

    if SONG_QUEUES[guild_id]:
        audio_url, title = SONG_QUEUES[guild_id].popleft()
        
        try:
            source = discord.FFmpegOpusAudio(audio_url, **FFMPEG_OPTIONS)
        except Exception as e:
            logger.error(f"Erro ao criar FFmpegOpusAudio para '{title}' ({audio_url}): {e}")
            await channel.send(f"❌ Erro ao processar áudio para **{title}**. Pulando para a próxima.")
            # Tenta tocar a próxima música se houver um erro com a atual
            asyncio.create_task(play_next_song(voice_client, guild_id, channel))
            return

        def after_play(error):
            """Callback executado após a música terminar ou um erro ocorrer."""
            if error:
                logger.error(f"Erro ao tocar '{title}': {error}")
                asyncio.run_coroutine_threadsafe(channel.send(f"❌ Erro ao tocar **{title}**: {error}"), bot.loop)
            
            # Garante que o loop de eventos do bot seja usado para a próxima chamada
            asyncio.run_coroutine_threadsafe(play_next_song(voice_client, guild_id, channel), bot.loop)

        voice_client.play(source, after=after_play)
        await channel.send(f"▶️ Tocando agora: **{title}**")
        start_inactivity_timer(guild_id, voice_client) # Reinicia o timer de inatividade

    else:
        logger.info(f"Fila vazia para a guilda {guild_id}. Iniciando temporizador de desconexão.")
        start_inactivity_timer(guild_id, voice_client) # Inicia o timer de inatividade quando a fila está vazia

def start_inactivity_timer(guild_id, voice_client):
    """Inicia ou reinicia o temporizador de desconexão por inatividade."""
    stop_inactivity_timer(guild_id) # Para qualquer timer existente
    
    async def disconnect_after_inactivity():
        await asyncio.sleep(300) # 5 minutos de inatividade
        if voice_client and voice_client.is_connected() and not voice_client.is_playing() and not SONG_QUEUES.get(guild_id):
            logger.info(f"Desconectando da guilda {guild_id} por inatividade.")
            await voice_client.disconnect()
            if guild_id in SONG_QUEUES:
                del SONG_QUEUES[guild_id]
            if guild_id in INACTIVITY_TIMERS:
                del INACTIVITY_TIMERS[guild_id]

    task = bot.loop.create_task(disconnect_after_inactivity())
    INACTIVITY_TIMERS[guild_id] = task
    logger.info(f"Temporizador de inatividade iniciado para a guilda {guild_id}.")

def stop_inactivity_timer(guild_id):
    """Para o temporizador de desconexão por inatividade, se existir."""
    if guild_id in INACTIVITY_TIMERS:
        INACTIVITY_TIMERS[guild_id].cancel()
        del INACTIVITY_TIMERS[guild_id]
        logger.info(f"Temporizador de inatividade parado para a guilda {guild_id}.")

# --- Eventos do Bot ---

@bot.event
async def on_ready():
    """Evento disparado quando o bot está pronto e conectado ao Discord."""
    await bot.tree.sync() # Sincroniza comandos de barra (slash commands)
    logger.info(f"🤖 {bot.user} está online!")

@bot.event
async def on_voice_state_update(member, before, after):
    """
    Monitora mudanças no estado de voz para gerenciar o temporizador de inatividade.
    Desconecta o bot se ele ficar sozinho no canal de voz.
    """
    if member == bot.user: # Se a mudança de estado for do próprio bot
        if before.channel and not after.channel: # Bot desconectou do canal de voz
            guild_id = before.channel.guild.id
            stop_inactivity_timer(guild_id)
            if guild_id in SONG_QUEUES:
                del SONG_QUEUES[guild_id]
            logger.info(f"Bot desconectado do canal de voz na guilda {guild_id}. Fila e timer limpos.")
        return

    # Se o bot está em um canal de voz
    if bot.user in before.channel.members if before.channel else []:
        if len(before.channel.members) == 1 and bot.user in before.channel.members:
            # Se o bot era o único no canal e alguém saiu (ou ele mesmo saiu)
            guild_id = before.channel.guild.id
            if not SONG_QUEUES.get(guild_id) or not SONG_QUEUES[guild_id]: # Se a fila estiver vazia
                start_inactivity_timer(guild_id, before.channel.guild.voice_client)
                logger.info(f"Bot ficou sozinho no canal de voz na guilda {guild_id}. Iniciando temporizador de inatividade.")
    
    # Se alguém entrou no canal onde o bot está e o bot estava inativo
    if after.channel and bot.user in after.channel.members and after.channel.guild.id in INACTIVITY_TIMERS:
        if after.channel.guild.voice_client and (after.channel.guild.voice_client.is_playing() or SONG_QUEUES.get(after.channel.guild.id)):
            stop_inactivity_timer(after.channel.guild.id)
            logger.info(f"Atividade detectada na guilda {after.channel.guild.id}. Temporizador de inatividade parado.")


# --- Comandos de Barra (Slash Commands) ---

@bot.tree.command(name="play", description="Toca uma música ou a adiciona à fila.")
@app_commands.describe(query="Nome da música ou URL do YouTube")
async def play(interaction: discord.Interaction, query: str):
    await interaction.response.defer() # Deferir a resposta para evitar timeout de interação

    voice_channel = interaction.user.voice.channel

    if voice_channel is None:
        await interaction.followup.send("Você precisa estar em um canal de voz para usar este comando.")
        return

    voice_client = interaction.guild.voice_client

    # Conecta ou move o bot para o canal de voz do usuário
    if voice_client is None:
        try:
            voice_client = await voice_channel.connect()
            logger.info(f"Bot conectado ao canal de voz '{voice_channel.name}' na guilda '{interaction.guild.name}'.")
        except Exception as e:
            logger.error(f"Erro ao conectar ao canal de voz: {e}")
            await interaction.followup.send(f"❌ Não foi possível conectar ao canal de voz: {e}")
            return
    elif voice_channel != voice_client.channel:
        try:
            await voice_client.move_to(voice_channel)
            logger.info(f"Bot movido para o canal de voz '{voice_channel.name}' na guilda '{interaction.guild.name}'.")
        except Exception as e:
            logger.error(f"Erro ao mover para o canal de voz: {e}")
            await interaction.followup.send(f"❌ Não foi possível mover para o canal de voz: {e}")
            return

    # Para qualquer temporizador de inatividade ativo
    stop_inactivity_timer(interaction.guild_id)

    # Busca a música com yt-dlp
    results = await search_ytdlp_async(query, YTDL_OPTIONS)
    tracks = results.get("entries", []) if results else []

    if not tracks:
        await interaction.followup.send(f"❌ Nenhuma música encontrada para '{query}'.")
        return

    # Pega a primeira faixa encontrada
    first_track = tracks[0]
    audio_url = first_track.get("url")
    title = first_track.get("title", "Título Desconhecido")
    uploader = first_track.get("uploader", "Desconhecido")

    if not audio_url:
        logger.warning(f"URL de áudio não encontrada para '{title}'.")
        await interaction.followup.send(f"❌ Não foi possível obter o URL de áudio para **{title}**.")
        return

    guild_id = str(interaction.guild_id)
    if guild_id not in SONG_QUEUES:
        SONG_QUEUES[guild_id] = deque()

    SONG_QUEUES[guild_id].append((audio_url, title, uploader))

    if voice_client.is_playing() or voice_client.is_paused():
        await interaction.followup.send(f"🎶 Adicionado à fila: **{title}** por **{uploader}**")
    else:
        await interaction.followup.send(f"▶️ Começando a tocar: **{title}** por **{uploader}**")
        await play_next_song(voice_client, guild_id, interaction.channel)


@bot.tree.command(name="skip", description="Pula a música atual.")
async def skip(interaction: discord.Interaction):
    voice_client = interaction.guild.voice_client

    if not voice_client or not voice_client.is_connected() or not voice_client.is_playing():
        await interaction.response.send_message("Não estou tocando nada para pular.")
        return

    voice_client.stop() # Isso aciona o callback after_play, que chamará play_next_song
    await interaction.response.send_message("⏭️ Música pulada.")


@bot.tree.command(name="pause", description="Pausa a música atual.")
async def pause(interaction: discord.Interaction):
    voice_client = interaction.guild.voice_client

    if not voice_client or not voice_client.is_connected():
        return await interaction.response.send_message("Não estou em um canal de voz.")

    if voice_client.is_playing():
        voice_client.pause()
        await interaction.response.send_message("⏸️ Reprodução pausada.")
    else:
        await interaction.response.send_message("Nada está tocando para pausar.")


@bot.tree.command(name="resume", description="Retoma a música pausada.")
async def resume(interaction: discord.Interaction):
    voice_client = interaction.guild.voice_client

    if not voice_client or not voice_client.is_connected():
        return await interaction.response.send_message("Não estou em um canal de voz.")

    if voice_client.is_paused():
        voice_client.resume()
        await interaction.response.send_message("▶️ Reprodução retomada.")
    else:
        await interaction.response.send_message("Nada está pausado para retomar.")


@bot.tree.command(name="stop", description="Para a reprodução e limpa a fila.")
async def stop(interaction: discord.Interaction):
    voice_client = interaction.guild.voice_client

    if not voice_client or not voice_client.is_connected():
        return await interaction.response.send_message("Não estou conectado a nenhum canal de voz.")

    guild_id_str = str(interaction.guild_id)
    if guild_id_str in SONG_QUEUES:
        SONG_QUEUES[guild_id_str].clear() # Limpa a fila

    if voice_client.is_playing() or voice_client.is_paused():
        voice_client.stop() # Isso aciona o after_play que vai ver a fila vazia e desconectar

    stop_inactivity_timer(interaction.guild_id) # Garante que o timer seja parado
    await voice_client.disconnect()
    await interaction.response.send_message("⏹️ Reprodução parada e desconectado.")


@bot.tree.command(name="queue", description="Mostra as músicas na fila.")
async def queue(interaction: discord.Interaction):
    guild_id_str = str(interaction.guild_id)
    if not SONG_QUEUES.get(guild_id_str):
        await interaction.response.send_message("A fila está vazia.")
        return

    queue_list = list(SONG_QUEUES[guild_id_str])
    if not queue_list:
        await interaction.response.send_message("A fila está vazia.")
        return

    queue_display = "Fila de Músicas:\n"
    for i, (url, title, uploader) in enumerate(queue_list):
        queue_display += f"**{i+1}.** {title} por {uploader}\n"
        if len(queue_display) > 1900: # Limite para evitar exceder o tamanho da mensagem do Discord
            queue_display += f"... e mais {len(queue_list) - (i+1)} músicas."
            break
    
    await interaction.response.send_message(queue_display)


@bot.tree.command(name="volume", description="Ajusta o volume do bot.")
@app_commands.describe(volume="Volume de 0 a 100")
async def volume(interaction: discord.Interaction, volume: int):
    voice_client = interaction.guild.voice_client

    if not voice_client or not voice_client.is_connected():
        return await interaction.response.send_message("Não estou em um canal de voz.")

    if not 0 <= volume <= 100:
        return await interaction.response.send_message("O volume deve ser entre 0 e 100.")

    voice_client.source.volume = volume / 100.0
    await interaction.response.send_message(f"🔊 Volume ajustado para {volume}%.")


@bot.tree.command(name="loop", description="Ativa/desativa o loop da música atual ou da fila.")
@app_commands.describe(mode="Modo de loop: 'current' (música atual), 'queue' (fila), 'off' (desativar)")
async def loop(interaction: discord.Interaction, mode: str):
    guild_id_str = str(interaction.guild_id)
    # Por simplicidade, vamos implementar apenas loop da música atual ou desativar.
    # Loop de fila exigiria um controle mais complexo do deque.

    if mode.lower() not in ["current", "off"]:
        return await interaction.response.send_message("Modo de loop inválido. Use 'current' ou 'off'.")

    # Não há um mecanismo de loop embutido no discord.py para FFmpegOpusAudio.
    # Para loop, precisaríamos re-adicionar a música à fila ou recriar a fonte.
    # Para este exemplo, vamos simplificar e focar em loop "manual" da música atual.
    
    # Se o loop da música atual for ativado, a música não será removida da fila.
    # Isso é uma simplificação, um loop real exigiria um controle mais granular.
    await interaction.response.send_message("Este comando está em desenvolvimento. Por favor, use o `/play` para adicionar músicas novamente.")


@bot.tree.command(name="remove", description="Remove uma música da fila pelo número.")
@app_commands.describe(number="Número da música na fila para remover")
async def remove(interaction: discord.Interaction, number: int):
    guild_id_str = str(interaction.guild_id)
    if not SONG_QUEUES.get(guild_id_str):
        await interaction.response.send_message("A fila está vazia.")
        return

    if not (1 <= number <= len(SONG_QUEUES[guild_id_str])):
        await interaction.response.send_message("Número da música inválido na fila.")
        return

    removed_song = SONG_QUEUES[guild_id_str].pop(number - 1)
    await interaction.response.send_message(f"🗑️ Removido da fila: **{removed_song[1]}**.")


# Inicia o bot com o TOKEN
if TOKEN is None:
    logger.error("O token do Discord não foi encontrado. Certifique-se de que a variável de ambiente 'TOKEN' está definida.")
else:
    bot.run(TOKEN)