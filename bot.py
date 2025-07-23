import os
import discord
from discord.ext import commands
import wavelink

# Configura as intenções do bot
intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)

# Pega dados das variáveis de ambiente (Railway)
TOKEN = os.getenv("TOKEN")
LAVALINK_HOST = os.getenv("LAVALINK_HOST") # Será o nome do serviço do Lavalink no Railway
LAVALINK_PASSWORD = os.getenv("LAVALINK_PASSWORD")
LAVALINK_PORT = os.getenv("LAVALINK_PORT") # Adicione esta linha para pegar a porta

@bot.event
async def on_ready():
    """
    Evento que é disparado quando o bot está pronto e conectado ao Discord.
    Aqui, ele tenta criar um nó Wavelink para se conectar ao servidor Lavalink.
    """
    print(f"🤖 Rã está online como {bot.user}")
    try:
        # Em wavelink v3, wavelink.Node é usado para criar um nó.
        # Para comunicação interna no Railway, use HTTP e a porta definida.
        # Use o nome do serviço Lavalink como host e a porta interna.
        node = wavelink.Node(
            uri=f"http://{LAVALINK_HOST}:{LAVALINK_PORT}", # URI completa para o servidor Lavalink interno
            password=LAVALINK_PASSWORD,
        )
        # Conecta o nó ao pool de nós do Wavelink
        await wavelink.Pool.connect(client=bot, nodes=[node])
        print(f"✅ Conectado ao Lavalink em {LAVALINK_HOST}:{LAVALINK_PORT}")
    except Exception as e:
        print(f"❌ Erro ao conectar ao Lavalink: {e}")
        print("Certifique-se de que as variáveis de ambiente LAVALINK_HOST, LAVALINK_PORT e LAVALINK_PASSWORD estão corretas.")
        print("Verifique também se o serviço Lavalink está rodando e acessível no Railway.")

@bot.command()
async def play(ctx: commands.Context, *, query: str):
    """
    Comando para tocar uma música.
    Uso: !play <URL ou termo de pesquisa>
    """
    if not ctx.author.voice:
        return await ctx.send("🐸 Entra em um canal de voz primeiro!")

    player: wavelink.Player = ctx.voice_client
    if not player:
        try:
            player = await ctx.author.voice.channel.connect(cls=wavelink.Player)
        except Exception as e:
            return await ctx.send(f"❌ Não foi possível conectar ao canal de voz: {e}")

    if player.is_playing():
        return await ctx.send("🎵 Já estou tocando algo! Use `!stop` para parar a música atual.")

    tracks = await wavelink.Track.search(query)

    if not tracks:
        return await ctx.send(f"❌ Nenhuma faixa encontrada para '{query}'")

    track = tracks[0] # Pega a primeira faixa encontrada

    try:
        await player.play(track)
        await ctx.send(f"▶️ Tocando: `{track.title}` de `{track.author}`")
    except Exception as e:
        await ctx.send(f"❌ Erro ao tocar: {e}")

@bot.command()
async def stop(ctx: commands.Context):
    """
    Comando para parar a música e desconectar o bot do canal de voz.
    """
    if ctx.voice_client:
        await ctx.voice_client.disconnect()
        await ctx.send("🐸 Desligado!")
    else:
        await ctx.send("Não estou em um canal de voz.")

# Inicia o bot com o TOKEN
bot.run(TOKEN)