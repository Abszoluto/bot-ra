import os
import discord
from discord.ext import commands
from discord import app_commands
import wavelink
from dotenv import load_dotenv
import asyncio
import logging
from collections import deque

# Configuração de logging para melhor depuração
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger('discord_music_bot')

# Variáveis de ambiente para tokens e outros dados sensíveis
load_dotenv()
TOKEN = os.getenv("TOKEN")
LAVALINK_HOST = os.getenv("LAVALINK_HOST") # Nome do serviço Lavalink no Railway
LAVALINK_PASSWORD = os.getenv("LAVALINK_PASSWORD")
LAVALINK_PORT = os.getenv("LAVALINK_PORT") # Porta interna do Lavalink no Railway

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

# --- Funções Auxiliares ---

async def play_next_song(voice_client: wavelink.Player, guild_id: int, channel: discord.TextChannel):
    """Reproduz a próxima música na fila ou desconecta se a fila estiver vazia."""
    # Para garantir que o bot não tente reproduzir se já estiver desconectado
    if not voice_client or not voice_client.is_connected():
        logger.info(f"Bot desconectado da guilda {guild_id}. Limpando fila.")
        if guild_id in SONG_QUEUES:
            SONG_QUEUES[guild_id].clear()
        return

    if SONG_QUEUES[guild_id]:
        track_info = SONG_QUEUES[guild_id].popleft()
        # track_info agora é um objeto wavelink.Playable
        
        try:
            await voice_client.play(track_info)
            await channel.send(f"▶️ Tocando agora: **{track_info.title}** por **{track_info.author}**")
            start_inactivity_timer(guild_id, voice_client) # Reinicia o timer de inatividade
        except Exception as e:
            logger.error(f"Erro ao tocar '{track_info.title}': {e}")
            await channel.send(f"❌ Erro ao tocar **{track_info.title}**: {e}. Pulando para a próxima.")
            # Tenta tocar a próxima música se houver um erro com a atual
            asyncio.create_task(play_next_song(voice_client, guild_id, channel))
    else:
        logger.info(f"Fila vazia para a guilda {guild_id}. Iniciando temporizador de desconexão.")
        start_inactivity_timer(guild_id, voice_client) # Inicia o timer de inatividade quando a fila está vazia

def start_inactivity_timer(guild_id: int, voice_client: wavelink.Player):
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

def stop_inactivity_timer(guild_id: int):
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

    # Conecta ao Lavalink
    try:
        node = wavelink.Node(
            uri=f"http://{LAVALINK_HOST}:{LAVALINK_PORT}", # URI completa para o servidor Lavalink interno
            password=LAVALINK_PASSWORD,
        )
        await wavelink.Pool.connect(client=bot, nodes=[node])
        logger.info(f"✅ Conectado ao Lavalink em {LAVALINK_HOST}:{LAVALINK_PORT}")
    except Exception as e:
        logger.error(f"❌ Erro ao conectar ao Lavalink: {e}")
        logger.error("Certifique-se de que as variáveis de ambiente LAVALINK_HOST, LAVALINK_PORT e LAVALINK_PASSWORD estão corretas.")
        logger.error("Verifique também se o serviço Lavalink está rodando e acessível no Railway.")

@bot.event
async def on_wavelink_node_ready(node: wavelink.Node):
    """Evento disparado quando um nó Wavelink está pronto."""
    logger.info(f"Wavelink Node '{node.identifier}' está pronto!")

@bot.event
async def on_wavelink_track_end(player: wavelink.Player, track: wavelink.Playable, reason):
    """Evento disparado quando uma faixa Wavelink termina."""
    guild_id = player.guild.id
    # O callback after_play do discord.FFmpegOpusAudio não é usado aqui.
    # Em vez disso, o wavelink.Player lida com o término da faixa e o evento on_wavelink_track_end.
    # Chamamos play_next_song diretamente aqui.
    asyncio.create_task(play_next_song(player, guild_id, player.channel))


@bot.event
async def on_voice_state_update(member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
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

    # Conecta ou move o bot para o canal de voz do usuário
    # Em Wavelink, você usa get_player para obter o player, ou connect para criar um novo.
    player: wavelink.Player = interaction.guild.voice_client
    if not player:
        try:
            player = await voice_channel.connect(cls=wavelink.Player)
            logger.info(f"Bot conectado ao canal de voz '{voice_channel.name}' na guilda '{interaction.guild.name}'.")
        except Exception as e:
            logger.error(f"Erro ao conectar ao canal de voz: {e}")
            await interaction.followup.send(f"❌ Não foi possível conectar ao canal de voz: {e}")
            return
    elif voice_channel != player.channel:
        try:
            await player.move_to(voice_channel)
            logger.info(f"Bot movido para o canal de voz '{voice_channel.name}' na guilda '{interaction.guild.name}'.")
        except Exception as e:
            logger.error(f"Erro ao mover para o canal de voz: {e}")
            await interaction.followup.send(f"❌ Não foi possível mover para o canal de voz: {e}")
            return

    # Para qualquer temporizador de inatividade ativo
    stop_inactivity_timer(interaction.guild_id)

    # Busca a música com Wavelink
    try:
        # wavelink.Playable.search() retorna uma lista de Playable
        tracks = await wavelink.Playable.search(query)
    except Exception as e:
        logger.error(f"Erro ao buscar com Wavelink para '{query}': {e}")
        await interaction.followup.send(f"❌ Erro ao buscar a música. Tente novamente mais tarde.")
        return

    if not tracks:
        await interaction.followup.send(f"❌ Nenhuma música encontrada para '{query}'.")
        return

    # Pega a primeira faixa encontrada
    first_track = tracks[0]
    
    guild_id = interaction.guild.id
    if guild_id not in SONG_QUEUES:
        SONG_QUEUES[guild_id] = deque()

    SONG_QUEUES[guild_id].append(first_track) # Adiciona o objeto Playable diretamente

    if player.is_playing() or player.is_paused():
        await interaction.followup.send(f"🎶 Adicionado à fila: **{first_track.title}** por **{first_track.author}**")
    else:
        await interaction.followup.send(f"▶️ Começando a tocar: **{first_track.title}** por **{first_track.author}**")
        await play_next_song(player, guild_id, interaction.channel)


@bot.tree.command(name="skip", description="Pula a música atual.")
async def skip(interaction: discord.Interaction):
    player: wavelink.Player = interaction.guild.voice_client

    if not player or not player.is_connected() or not player.is_playing():
        await interaction.response.send_message("Não estou tocando nada para pular.")
        return

    await player.stop() # Isso aciona o on_wavelink_track_end, que chamará play_next_song
    await interaction.response.send_message("⏭️ Música pulada.")


@bot.tree.command(name="pause", description="Pausa a música atual.")
async def pause(interaction: discord.Interaction):
    player: wavelink.Player = interaction.guild.voice_client

    if not player or not player.is_connected():
        return await interaction.response.send_message("Não estou em um canal de voz.")

    if player.is_playing():
        await player.pause()
        await interaction.response.send_message("⏸️ Reprodução pausada.")
    else:
        await interaction.response.send_message("Nada está tocando para pausar.")


@bot.tree.command(name="resume", description="Retoma a música pausada.")
async def resume(interaction: discord.Interaction):
    player: wavelink.Player = interaction.guild.voice_client

    if not player or not player.is_connected():
        return await interaction.response.send_message("Não estou em um canal de voz.")

    if player.is_paused():
        await player.resume()
        await interaction.response.send_message("▶️ Reprodução retomada.")
    else:
        await interaction.response.send_message("Nada está pausado para retomar.")


@bot.tree.command(name="stop", description="Para a reprodução e limpa a fila.")
async def stop(interaction: discord.Interaction):
    player: wavelink.Player = interaction.guild.voice_client

    if not player or not player.is_connected():
        return await interaction.response.send_message("Não estou conectado a nenhum canal de voz.")

    guild_id = interaction.guild.id
    if guild_id in SONG_QUEUES:
        SONG_QUEUES[guild_id].clear() # Limpa a fila

    if player.is_playing() or player.is_paused():
        await player.stop() # Isso aciona o on_wavelink_track_end, que vai ver a fila vazia e desconectar

    stop_inactivity_timer(guild_id) # Garante que o timer seja parado
    await player.disconnect()
    await interaction.response.send_message("⏹️ Reprodução parada e desconectado.")


@bot.tree.command(name="queue", description="Mostra as músicas na fila.")
async def queue(interaction: discord.Interaction):
    guild_id = interaction.guild.id
    if not SONG_QUEUES.get(guild_id):
        await interaction.response.send_message("A fila está vazia.")
        return

    queue_list = list(SONG_QUEUES[guild_id])
    if not queue_list:
        await interaction.response.send_message("A fila está vazia.")
        return

    queue_display = "Fila de Músicas:\n"
    for i, track in enumerate(queue_list):
        queue_display += f"**{i+1}.** {track.title} por {track.author}\n"
        if len(queue_display) > 1900: # Limite para evitar exceder o tamanho da mensagem do Discord
            queue_display += f"... e mais {len(queue_list) - (i+1)} músicas."
            break
    
    await interaction.response.send_message(queue_display)


@bot.tree.command(name="volume", description="Ajusta o volume do bot.")
@app_commands.describe(volume="Volume de 0 a 100")
async def volume(interaction: discord.Interaction, volume: int):
    player: wavelink.Player = interaction.guild.voice_client

    if not player or not player.is_connected():
        return await interaction.response.send_message("Não estou em um canal de voz.")

    if not 0 <= volume <= 100:
        return await interaction.response.send_message("O volume deve ser entre 0 e 100.")

    await player.set_volume(volume)
    await interaction.response.send_message(f"🔊 Volume ajustado para {volume}%.")


@bot.tree.command(name="loop", description="Ativa/desativa o loop da música atual ou da fila.")
@app_commands.describe(mode="Modo de loop: 'current' (música atual), 'queue' (fila), 'off' (desativar)")
async def loop(interaction: discord.Interaction, mode: str):
    player: wavelink.Player = interaction.guild.voice_client
    guild_id = interaction.guild.id

    if not player or not player.is_connected():
        return await interaction.response.send_message("Não estou em um canal de voz.")

    if mode.lower() == "current":
        if player.current:
            # Para loop da música atual, adicionamos a música de volta ao início da fila.
            # Se a fila já está vazia, o play_next_song não a removeria de qualquer forma.
            SONG_QUEUES[guild_id].appendleft(player.current)
            await interaction.response.send_message(f"🔁 Loop da música atual ativado: **{player.current.title}**.")
        else:
            await interaction.response.send_message("Nenhuma música tocando para ativar o loop.")
    elif mode.lower() == "queue":
        # Para loop da fila, precisamos garantir que as músicas voltem para o final da fila após serem tocadas.
        # Isso exigiria modificar a lógica de `play_next_song` para adicionar a música de volta.
        # Por simplicidade, vamos manter a sugestão de re-adicionar manualmente por enquanto.
        await interaction.response.send_message("O loop da fila não está totalmente implementado para este bot. Por favor, use `/play` para adicionar músicas novamente se desejar repetir a fila.")
    elif mode.lower() == "off":
        # Não precisamos fazer nada específico para 'off' se não houver um estado de loop persistente.
        await interaction.response.send_message("Loop desativado.")
    else:
        await interaction.response.send_message("Modo de loop inválido. Use 'current', 'queue' ou 'off'.")


@bot.tree.command(name="remove", description="Remove uma música da fila pelo número.")
@app_commands.describe(number="Número da música na fila para remover")
async def remove(interaction: discord.Interaction, number: int):
    guild_id = interaction.guild.id
    if not SONG_QUEUES.get(guild_id):
        await interaction.response.send_message("A fila está vazia.")
        return

    if not (1 <= number <= len(SONG_QUEUES[guild_id])):
        await interaction.response.send_message("Número da música inválido na fila.")
        return

    try:
        removed_track = SONG_QUEUES[guild_id].pop(number - 1)
        await interaction.response.send_message(f"🗑️ Removido da fila: **{removed_track.title}**.")
    except IndexError: # Caso a fila seja modificada entre a verificação e a remoção
        await interaction.response.send_message("Erro ao remover a música. A fila pode ter sido alterada.")


# Inicia o bot com o TOKEN
if TOKEN is None:
    logger.error("O token do Discord não foi encontrado. Certifique-se de que a variável de ambiente 'TOKEN' está definida.")
else:
    bot.run(TOKEN)