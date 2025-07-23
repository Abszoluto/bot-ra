import os
import discord
from discord.ext import commands
import wavelink # Importa o módulo wavelink

# Configura as intenções do bot
intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)

# Pega dados das variáveis de ambiente (Railway)
TOKEN = os.getenv("TOKEN")
LAVALINK_HOST = os.getenv("LAVALINK_HOST")
LAVALINK_PORT = int(os.getenv("LAVALINK_PORT"))
LAVALINK_PASSWORD = os.getenv("LAVALINK_PASSWORD")

@bot.event
async def on_ready():
    """
    Evento que é disparado quando o bot está pronto e conectado ao Discord.
    Aqui, ele tenta criar um nó Wavelink para se conectar ao servidor Lavalink.
    """
    print(f"🤖 Rã está online como {bot.user}")
    try:
        # Em wavelink v3, NodePool.create_node foi substituído por wavelink.Node.create_node
        node = wavelink.Node(
            uri=f"http://{LAVALINK_HOST}:{LAVALINK_PORT}", # URI completa para o servidor Lavalink
            password=LAVALINK_PASSWORD,
        )
        await wavelink.Pool.connect(client=bot, nodes=[node])
        print(f"✅ Conectado ao Lavalink em {LAVALINK_HOST}:{LAVALINK_PORT}")
    except Exception as e:
        print(f"❌ Erro ao conectar ao Lavalink: {e}")

@bot.event
async def on_wavelink_node_ready(node: wavelink.Node):
    """
    Evento disparado quando um nó Wavelink está pronto para uso.
    """
    print(f"Wavelink Node '{node.identifier}' está pronto!")

@bot.command()
async def play(ctx: commands.Context, *, query: str):
    """
    Comando para tocar uma música.
    Uso: !play <URL ou termo de pesquisa>
    """
    if not ctx.author.voice:
        return await ctx.send("🐸 Entra em um canal de voz primeiro!")

    # Conecta-se ao canal de voz do autor, se ainda não estiver conectado
    # Em wavelink v3, o player é obtido através de ctx.guild.voice_client
    # ou conectando um novo.
    player: wavelink.Player = ctx.voice_client
    if not player:
        player = await ctx.author.voice.channel.connect(cls=wavelink.Player)
        # Certifique-se de que o player está conectado antes de tentar reproduzir
        if not player:
            return await ctx.send("❌ Não foi possível conectar ao canal de voz.")

    # Pesquisa a faixa. Em wavelink v3, usa wavelink.Track.search
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