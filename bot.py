import os
import discord
from discord.ext import commands
from discord import app_commands
import wavelink
from dotenv import load_dotenv
import asyncio
import logging
from collections import deque

# --- Configuração Inicial ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger('discord_music_bot')
load_dotenv()
TOKEN = os.getenv("TOKEN")
LAVALINK_HOST = os.getenv("LAVALINK_HOST")
LAVALINK_PASSWORD = os.getenv("LAVALINK_PASSWORD")
LAVALINK_PORT = os.getenv("LAVALINK_PORT")
intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True

# --- Classe Principal do Bot ---
class MusicBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)
        self.song_queues = {}
        self.inactivity_timers = {}

    async def setup_hook(self) -> None:
        await self.tree.sync()
        logger.info("Comandos de barra (/) sincronizados!")
        try:
            if not all([LAVALINK_HOST, LAVALINK_PORT, LAVALINK_PASSWORD]):
                logger.error("Uma ou mais variáveis de ambiente do Lavalink não foram definidas!")
                return
            node = wavelink.Node(uri=f"http://{LAVALINK_HOST}:{int(LAVALINK_PORT)}", password=LAVALINK_PASSWORD)
            await wavelink.Pool.connect(client=self, nodes=[node])
        except Exception as e:
            logger.error(f"❌ Falha fatal ao conectar ao Lavalink: {e}")

    async def play_next_song(self, player: wavelink.Player, guild_id: int, channel: discord.TextChannel):
        if not player or not player.is_connected():
            if guild_id in self.song_queues: self.song_queues[guild_id].clear()
            return

        if self.song_queues.get(guild_id):
            track = self.song_queues[guild_id].popleft()
            try:
                await player.play(track)
                await channel.send(f"▶️ Tocando agora: **{track.title}** por **{track.author}**")
                self.start_inactivity_timer(guild_id, player)
            except Exception as e:
                logger.error(f"Erro ao tocar '{track.title}': {e}")
                await channel.send(f"❌ Erro ao tocar **{track.title}**. Pulando para a próxima.")
                await self.play_next_song(player, guild_id, channel)
        else:
            self.start_inactivity_timer(guild_id, player)

    def start_inactivity_timer(self, guild_id: int, player: wavelink.Player):
        self.stop_inactivity_timer(guild_id)
        async def disconnect_after_inactivity():
            await asyncio.sleep(300)
            if player and player.is_connected() and not player.is_playing() and not self.song_queues.get(guild_id):
                await player.disconnect()
                if guild_id in self.song_queues: del self.song_queues[guild_id]
                if guild_id in self.inactivity_timers: del self.inactivity_timers[guild_id]
        task = self.loop.create_task(disconnect_after_inactivity())
        self.inactivity_timers[guild_id] = task

    def stop_inactivity_timer(self, guild_id: int):
        if guild_id in self.inactivity_timers:
            self.inactivity_timers[guild_id].cancel()
            del self.inactivity_timers[guild_id]

    async def on_ready(self):
        logger.info(f"🤖 {self.user} está online e pronto!")

    async def on_wavelink_node_ready(self, payload: wavelink.NodeReadyEventPayload):
        node = payload.node
        logger.info(f"✅ Conectado ao nó Lavalink '{node.identifier}' em {node.uri}")

    async def on_wavelink_track_end(self, payload: wavelink.TrackEndEventPayload):
        player = payload.player
        if payload.reason.should_play_next():
            if player.channel:
                 await self.play_next_song(player, player.guild.id, player.channel)
    
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        if member.bot: return
        player: wavelink.Player = member.guild.voice_client
        if not player or not before.channel: return
        if len(before.channel.members) == 1 and self.user in before.channel.members:
            self.start_inactivity_timer(member.guild.id, player)
        elif after.channel == player.channel and self.user in after.channel.members:
            if member.guild.id in self.inactivity_timers:
                self.stop_inactivity_timer(member.guild.id)

bot = MusicBot()

# --- Comandos de Barra (Slash Commands) ---

@bot.tree.command(name="play", description="Toca uma música ou a adiciona à fila.")
@app_commands.describe(query="Nome da música ou URL do YouTube/Spotify")
async def play(interaction: discord.Interaction, query: str):
    await interaction.response.defer()

    if not interaction.user.voice:
        await interaction.followup.send("Você precisa estar em um canal de voz para usar este comando.")
        return

    # -----------> INÍCIO DA CORREÇÃO <-----------
    # Lógica de conexão e movimento do player foi reescrita para ser mais robusta
    player: wavelink.Player = interaction.guild.voice_client
    user_voice_channel = interaction.user.voice.channel

    try:
        if not player:
            # Se não houver player, conecta ao canal do usuário
            player = await user_voice_channel.connect(cls=wavelink.Player)
        elif player.channel != user_voice_channel:
            # Se o player estiver em outro canal, move para o canal do usuário
            await player.move_to(user_voice_channel)
    except (asyncio.TimeoutError, wavelink.exceptions.ChannelTimeoutException):
        await interaction.followup.send("❌ Não foi possível conectar ao canal de voz (timeout). Tente novamente.")
        return
    # -----------> FIM DA CORREÇÃO <-----------
    
    player.channel = interaction.channel
    interaction.client.stop_inactivity_timer(interaction.guild_id)

    try:
        tracks: wavelink.Search = await wavelink.Playable.search(query)
        if not tracks:
            await interaction.followup.send(f"❌ Nenhuma música encontrada para '{query}'.")
            return
    except wavelink.LavalinkLoadException:
        await interaction.followup.send("❌ Ocorreu um erro ao tentar buscar a música. O serviço de música pode estar sobrecarregado. Tente novamente.")
        return
    except Exception as e:
        logger.error(f"Um erro inesperado ocorreu durante a busca: {e}")
        await interaction.followup.send("❌ Um erro inesperado ocorreu. Tente novamente.")
        return

    track = tracks[0]
    
    guild_id = interaction.guild_id
    if guild_id not in interaction.client.song_queues:
        interaction.client.song_queues[guild_id] = deque()
    
    interaction.client.song_queues[guild_id].append(track)

    if player.is_playing() or player.is_paused():
        await interaction.followup.send(f"🎶 Adicionado à fila: **{track.title}**")
    else:
        await interaction.followup.send(f"Buscando: **{track.title}**...")
        await interaction.client.play_next_song(player, guild_id, interaction.channel)


# ... (restante dos comandos: skip, pause, resume, etc., permanecem os mesmos) ...

@bot.tree.command(name="skip", description="Pula a música atual.")
async def skip(interaction: discord.Interaction):
    player: wavelink.Player = interaction.guild.voice_client
    if not player or not player.is_playing():
        return await interaction.response.send_message("Não há nada tocando para pular.")
    
    await player.skip(force=True)
    await interaction.response.send_message("⏭️ Música pulada.")

@bot.tree.command(name="pause", description="Pausa a música atual.")
async def pause(interaction: discord.Interaction):
    player: wavelink.Player = interaction.guild.voice_client
    if not player or not player.is_playing():
        return await interaction.response.send_message("Não há nada tocando para pausar.")
    await player.pause(True)
    await interaction.response.send_message("⏸️ Reprodução pausada.")

@bot.tree.command(name="resume", description="Retoma a música pausada.")
async def resume(interaction: discord.Interaction):
    player: wavelink.Player = interaction.guild.voice_client
    if not player or not player.is_paused():
        return await interaction.response.send_message("Não há nada pausado para retomar.")
    await player.pause(False)
    await interaction.response.send_message("▶️ Reprodução retomada.")

@bot.tree.command(name="stop", description="Para a reprodução, limpa a fila e desconecta.")
async def stop(interaction: discord.Interaction):
    player: wavelink.Player = interaction.guild.voice_client
    if not player:
        return await interaction.response.send_message("Não estou conectado a um canal de voz.")
    
    guild_id = interaction.guild_id
    if guild_id in interaction.client.song_queues:
        interaction.client.song_queues[guild_id].clear()
    
    await player.disconnect()
    await interaction.response.send_message("⏹️ Reprodução parada. Fila limpa e bot desconectado.")

@bot.tree.command(name="queue", description="Mostra as músicas na fila.")
async def queue(interaction: discord.Interaction):
    guild_id = interaction.guild_id
    queue = interaction.client.song_queues.get(guild_id)
    if not queue:
        return await interaction.response.send_message("A fila está vazia.")
    
    embed = discord.Embed(title="Fila de Músicas", color=discord.Color.blue())
    queue_text = ""
    for i, track in enumerate(list(queue)[:10]):
        queue_text += f"**{i+1}.** {track.title} - `{track.author}`\n"
    
    embed.description = queue_text
    if len(queue) > 10:
        embed.set_footer(text=f"... e mais {len(queue) - 10} música(s).")
        
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="volume", description="Ajusta o volume do bot (0 a 100).")
@app_commands.describe(level="Nível do volume de 0 a 100")
async def volume(interaction: discord.Interaction, level: int):
    player: wavelink.Player = interaction.guild.voice_client
    if not player:
        return await interaction.response.send_message("Não estou em um canal de voz.")
    if not 0 <= level <= 100:
        return await interaction.response.send_message("O volume deve ser um valor entre 0 e 100.")
    
    await player.set_volume(level)
    await interaction.response.send_message(f"🔊 Volume ajustado para {level}%.")

# --- Execução do Bot ---
if __name__ == "__main__":
    if TOKEN is None:
        logger.error("O token do Discord não foi encontrado. Certifique-se de que a variável de ambiente 'TOKEN' está definida.")
    else:
        bot.run(TOKEN)