import os
import discord
from discord.ext import commands
import wavelink

intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)

# Pega dados das variáveis de ambiente (Railway)
TOKEN = os.getenv("TOKEN")
LAVALINK_HOST = os.getenv("LAVALINK_HOST")
LAVALINK_PORT = int(os.getenv("LAVALINK_PORT"))
LAVALINK_PASSWORD = os.getenv("LAVALINK_PASSWORD")

@bot.event
async def on_ready():
    print(f"🤖 Rã está online como {bot.user}")
    await wavelink.NodePool.create_node(
        bot=bot,
        host=LAVALINK_HOST,
        port=LAVALINK_PORT,
        password=LAVALINK_PASSWORD,
        https=False,
        spotify_client=None,
    )

@bot.command()
async def play(ctx, *, url: str):
    if not ctx.author.voice:
        return await ctx.send("🐸 Entra em um canal de voz primeiro!")

    vc = ctx.voice_client
    if not vc:
        vc = await ctx.author.voice.channel.connect(cls=wavelink.Player)

    if vc.is_playing():
        return await ctx.send("🎵 Já estou tocando algo!")

    try:
        track = await wavelink.Playable.search(url, source="auto")
        await vc.play(track)
        await ctx.send(f"▶️ Tocando: `{track.title}`")
    except Exception as e:
        await ctx.send(f"Erro ao tocar: {e}")

@bot.command()
async def stop(ctx):
    if ctx.voice_client:
        await ctx.voice_client.disconnect()
        await ctx.send("🐸 Desligado!")
    else:
        await ctx.send("Não estou em um canal de voz.")

bot.run(TOKEN)