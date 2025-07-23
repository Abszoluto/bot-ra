import os
import discord
from discord.ext import commands
import wavelink

# Configura as intenções do bot
intents = discord.Intents.all()
# Alterado o prefixo do comando para '?'
bot = commands.Bot(command_prefix="?", intents=intents) 

# Pega dados das variáveis de ambiente (Railway)
TOKEN = os.getenv("TOKEN")
LAVALINK_HOST = os.getenv("LAVALINK_HOST") # Será o nome do serviço do Lavalink no Railway
LAVALINK_PASSWORD = os.getenv("LAVALINK_PASSWORD")
LAVALINK_PORT = os.getenv("LAVALINK_PORT") # Adicione esta linha para pegar a porta

@bot.event
async def on_ready():
    """
    Evento que é disparado quando o bot está pronto e conectado ao Discord.
    Aqui, ele tenta criar um um nó Wavelink para se conectar ao servidor Lavalink.
    """
    print(f"🤖 Rã está online como {bot.user}")
    try:
        # Utiliza wavelink.NodePool.create_node conforme o exemplo fornecido,
        # mas com as variáveis de ambiente para host, porta e senha.
        node = await wavelink.NodePool.create_node(
            bot=bot,
            host=LAVALINK_HOST,
            port=int(LAVALINK_PORT), # Garante que a porta seja um inteiro
            password=LAVALINK_PASSWORD,
            # Para comunicação interna no Railway, não é necessário SSL.
            # Se o Lavalink estivesse configurado para SSL, você usaria `uri=f"https://{LAVALINK_HOST}:{LAVALINK_PORT}"`
            # e talvez `secure=True` se o wavelink.NodePool.create_node suportasse diretamente.
            # No entanto, para Railway, HTTP interno é o padrão e mais simples.
        )
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

    # Obtém o player existente ou conecta-se ao canal de voz do autor
    # Certifica-se de que há um nó conectado antes de tentar obter o player
    if not wavelink.NodePool.nodes:
        return await ctx.send("❌ Nenhum nó Lavalink conectado. Por favor, aguarde ou verifique a configuração.")

    player: wavelink.Player = ctx.voice_client
    if not player:
        try:
            player = await ctx.author.voice.channel.connect(cls=wavelink.Player)
        except Exception as e:
            return await ctx.send(f"❌ Não foi possível conectar ao canal de voz: {e}")

    if player.is_playing():
        return await ctx.send("🎵 Já estou tocando algo! Use `?stop` para parar a música atual.") # Atualizado para o novo prefixo

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