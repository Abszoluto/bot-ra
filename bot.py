import os
import discord
from discord.ext import commands
from discord import app_commands
from dotenv import load_dotenv
import yt_dlp # NEW
from collections import deque # NEW
import asyncio # NEW

# Environment variables for tokens and other sensitive data
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN") # Alterado para DISCORD_TOKEN para consistência

# Create the structure for queueing songs - Dictionary of queues
SONG_QUEUES = {}

async def search_ytdlp_async(query, ydl_opts):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: _extract(query, ydl_opts))

def _extract(query, ydl_opts):
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        return ydl.extract_info(query, download=False)


# Setup of intents. Intents are permissions the bot has on the server
intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True # Necessário para interagir com canais de voz

# Bot setup
bot = commands.Bot(command_prefix="!", intents=intents) # Mantendo '!' como prefixo para comandos de texto

# Bot ready-up code
@bot.event
async def on_ready():
    await bot.tree.sync() # Sincroniza comandos de barra (slash commands)
    print(f"{bot.user} is online!")


@bot.tree.command(name="skip", description="Skips the current playing song")
async def skip(interaction: discord.Interaction):
    if interaction.guild.voice_client and (interaction.guild.voice_client.is_playing() or interaction.guild.voice_client.is_paused()):
        interaction.guild.voice_client.stop()
        await interaction.response.send_message("Skipped the current song.")
    else:
        await interaction.response.send_message("Not playing anything to skip.")


@bot.tree.command(name="pause", description="Pause the currently playing song.")
async def pause(interaction: discord.Interaction):
    voice_client = interaction.guild.voice_client

    # Check if the bot is in a voice channel
    if voice_client is None:
        return await interaction.response.send_message("I'm not in a voice channel.")

    # Check if something is actually playing
    if not voice_client.is_playing():
        return await interaction.response.send_message("Nothing is currently playing.")
    
    # Pause the track
    voice_client.pause()
    await interaction.response.send_message("Playback paused!")


@bot.tree.command(name="resume", description="Resume the currently paused song.")
async def resume(interaction: discord.Interaction):
    voice_client = interaction.guild.voice_client

    # Check if the bot is in a voice channel
    if voice_client is None:
        return await interaction.response.send_message("I'm not in a voice channel.")

    # Check if it's actually paused
    if not voice_client.is_paused():
        return await interaction.response.send_message("I’m not paused right now.")
    
    # Resume playback
    voice_client.resume()
    await interaction.response.send_message("Playback resumed!")


@bot.tree.command(name="stop", description="Stop playback and clear the queue.")
async def stop(interaction: discord.Interaction):
    voice_client = interaction.guild.voice_client

    # Check if the bot is in a voice channel
    if not voice_client or not voice_client.is_connected():
        return await interaction.response.send_message("I'm not connected to any voice channel.")

    # Clear the guild's queue
    guild_id_str = str(interaction.guild_id)
    if guild_id_str in SONG_QUEUES:
        SONG_QUEUES[guild_id_str].clear()

    # If something is playing or paused, stop it
    if voice_client.is_playing() or voice_client.is_paused():
        voice_client.stop()

    # (Optional) Disconnect from the channel
    await voice_client.disconnect()

    await interaction.response.send_message("Stopped playback and disconnected!")


@bot.tree.command(name="play", description="Play a song or add it to the queue.")
@app_commands.describe(song_query="Search query")
async def play(interaction: discord.Interaction, song_query: str):
    await interaction.response.defer()

    voice_channel = interaction.user.voice.channel

    if voice_channel is None:
        await interaction.followup.send("You must be in a voice channel.")
        return

    voice_client = interaction.guild.voice_client

    if voice_client is None:
        voice_client = await voice_channel.connect()
    elif voice_channel != voice_client.channel:
        await voice_client.move_to(voice_channel)

    ydl_options = {
        "format": "bestaudio[abr<=96]/bestaudio", # Ajustado para um bitrate mais baixo para otimização
        "noplaylist": True,
        "youtube_include_dash_manifest": False,
        "youtube_include_hls_manifest": False,
        "quiet": True, # Suprime a saída padrão do yt-dlp
        "no_warnings": True, # Suprime avisos do yt-dlp
    }

    query = "ytsearch1: " + song_query
    results = await search_ytdlp_async(query, ydl_options)
    tracks = results.get("entries", [])

    if not tracks: # Alterado de tracks is None para not tracks
        await interaction.followup.send("No results found.")
        return

    first_track = tracks[0]
    audio_url = first_track["url"]
    title = first_track.get("title", "Untitled")

    guild_id = str(interaction.guild_id)
    if SONG_QUEUES.get(guild_id) is None:
        SONG_QUEUES[guild_id] = deque()

    SONG_QUEUES[guild_id].append((audio_url, title))

    if voice_client.is_playing() or voice_client.is_paused():
        await interaction.followup.send(f"Added to queue: **{title}**")
    else:
        await interaction.followup.send(f"Now playing: **{title}**")
        await play_next_song(voice_client, guild_id, interaction.channel)


async def play_next_song(voice_client, guild_id, channel):
    if SONG_QUEUES[guild_id]:
        audio_url, title = SONG_QUEUES[guild_id].popleft()

        ffmpeg_options = {
            "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
            "options": "-vn -c:a libopus -b:a 96k",
            # Certifique-se de que o caminho para o FFmpeg está correto no ambiente do Railway
            "executable": "bin/ffmpeg/ffmpeg" # Caminho para Linux, ajuste se necessário
        }

        # Adicionado tratamento de erro para FFmpegOpusAudio
        try:
            source = discord.FFmpegOpusAudio(audio_url, **ffmpeg_options)
        except Exception as e:
            print(f"Erro ao criar FFmpegOpusAudio para {title}: {e}")
            asyncio.create_task(channel.send(f"❌ Erro ao processar áudio para **{title}**: {e}"))
            # Tenta tocar a próxima música se houver um erro com a atual
            asyncio.run_coroutine_threadsafe(play_next_song(voice_client, guild_id, channel), bot.loop)
            return

        def after_play(error):
            if error:
                print(f"Error playing {title}: {error}")
                asyncio.run_coroutine_threadsafe(channel.send(f"❌ Erro ao tocar **{title}**: {error}"), bot.loop)
            asyncio.run_coroutine_threadsafe(play_next_song(voice_client, guild_id, channel), bot.loop)

        voice_client.play(source, after=after_play)
        asyncio.create_task(channel.send(f"Now playing: **{title}**"))
    else:
        await voice_client.disconnect()
        # Não é necessário limpar a fila aqui, pois ela já estaria vazia
        # SONG_QUEUES[guild_id] = deque() # Removido, pois a fila já está vazia aqui


# Run the bot
bot.run(TOKEN)