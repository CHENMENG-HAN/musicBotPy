from typing import Final, Optional, List, Dict, Any
from dotenv import load_dotenv
from discord import Intents, Client, Message
from discord.ext import commands
from discord.ui import View, Button, Select
from yt_dlp import YoutubeDL
from spotipy import Spotify
from spotipy.oauth2 import SpotifyClientCredentials
import discord, os, asyncio, yt_dlp, random, logging
import json
import os

load_dotenv()
TOKEN: Final[str] = os.getenv('DISCORD_TOKEN')

intents: Final[Intents] = Intents.default()
intents.message_content = True
intents.reactions = True
bot: Final[commands.Bot] = commands.Bot(command_prefix='!', intents=intents)
logging.basicConfig(level=logging.INFO)
queue: List[Dict[str, Any]] = []
queue_info: List[Dict[str, Any]] = []
is_playing: bool = False
volume: float = 0.8
current_song: Optional[str] = None
current_song_info: Dict[str, Any] = {}
play_next_lock = asyncio.Lock()
loop = False
repeat = True

#spotify
SPOTIFY_CLIENT_ID = os.getenv('SPOTIFY_CLIENT_ID')
SPOTIFY_CLIENT_SECRET = os.getenv('SPOTIFY_CLIENT_SECRET')
spotify_credentials_manager = SpotifyClientCredentials(client_id=SPOTIFY_CLIENT_ID, client_secret=SPOTIFY_CLIENT_SECRET)
spotify_client = Spotify(client_credentials_manager=spotify_credentials_manager)
#spotify

ytdl_format_options: Final[Dict[str, Any]] = {
    'format': 'bestaudio/best',
    'noplaylist': False,  # Allow playlists
    'quiet': True,
    'extractaudio': True,
    'audioformat': 'mp3',
    'outtmpl': '%(extractor)s-%(id)s-%(title)s.%(ext)s',
    'restrictfilenames': True,
    'nocheckcertificate': True,
    'ignoreerrors': False,
    'logtostderr': False,
    'no_warnings': True,
    'default_search': 'auto',
    'source_address': '0.0.0.0',  # Bind to ipv4 since ipv6 addresses cause issues sometimes
    'cachedir': False,  # Disable caching for simplicity
}

def get_ffmpeg_options(volume: float) -> Dict[str, str]:
    return {
        'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
        'options': f'-vn -b:a 128k -af "volume={volume}"',  # Adjust volume dynamically
    }

ytdl: Final[yt_dlp.YoutubeDL] = yt_dlp.YoutubeDL(ytdl_format_options)

@bot.event
async def on_ready() -> None:
    print(f'{bot.user} is now working!')

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        await ctx.send("未知的指令. 使用 `!help` 查看可用的指令")
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("缺少必要的值 請查看指令使用方式")
    elif isinstance(error, commands.BadArgument):
        await ctx.send("錯誤的值 請查看指令使用方式")
    else:
        await ctx.send(f"錯誤: {str(error)}")
        logging.error(f"錯誤: {str(error)}")

@bot.event
async def on_interaction(interaction: discord.Interaction):
    if interaction.data["custom_id"] == "select_search_result":
        index = int(interaction.data["values"][0])
        selected_result = interaction.message.embeds[0].fields[index]
        title = selected_result.name.split(". ")[1]
        url = selected_result.value.split("](")[1][:-1]

        queue.append({'url': url, 'title': title})
        queue_info.append({'url': url, 'title': title})  # Add to queue info for repeating

        await interaction.response.send_message(f'新增 {title} 至播放清單')

        if not is_playing:
            await play_next(interaction)
        else:
            await now_playing(interaction)

class NowPlayingView(View):
    def __init__(self):
        super().__init__(timeout=600.0)

    @discord.ui.button(label="⏸️ 暫停", style=discord.ButtonStyle.primary, custom_id="pause_button")
    async def pause_button(self, interaction: discord.Interaction, button: Button):
        voice_client = interaction.guild.voice_client
        if voice_client.is_playing():
            voice_client.pause()
            await interaction.response.edit_message(content='Paused the current song.', view=self)
        else:
            await interaction.response.edit_message(content='No song is currently playing.', view=self)

    @discord.ui.button(label="▶️ 播放", style=discord.ButtonStyle.success, custom_id="resume_button")
    async def resume_button(self, interaction: discord.Interaction, button: Button):
        voice_client = interaction.guild.voice_client
        if voice_client.is_paused():
            voice_client.resume()
            await interaction.response.edit_message(content='Resumed the current song.', view=self)
        else:
            await interaction.response.edit_message(content='No song is currently paused.', view=self)

    @discord.ui.button(label="⏭️ 跳過", style=discord.ButtonStyle.danger, custom_id="skip_button")
    async def skip_button(self, interaction: discord.Interaction, button: Button):
        voice_client = interaction.guild.voice_client
        if voice_client.is_playing():
            voice_client.stop()
            await interaction.response.edit_message(content='Skipping the current song...', view=self)
        else:
            await interaction.response.edit_message(content='No song is currently playing.', view=self)

    @discord.ui.button(label="🔁 循環歌曲", style=discord.ButtonStyle.secondary, custom_id="loop_button")
    async def loop_button(self, interaction: discord.Interaction, button: Button):
        global loop
        loop = not loop
        loop_status = "enabled" if loop else "disabled"
        await interaction.response.edit_message(content=f'Looping is now {loop_status}.', view=self)    

play_next_lock = asyncio.Lock()

async def play_next(ctx_or_interaction):
    global is_playing, current_song, current_song_info, queue, queue_info, repeat

    if loop and current_song:
        queue.insert(0, current_song_info)

    # Refill the queue from queue_info if it's empty and repeat is enabled
    if not queue and repeat:
        queue = queue_info.copy()

    if queue:
        is_playing = True
        song = queue.pop(0)
        if repeat and song not in queue_info:
            queue_info.append(song)

        m_url = song['url']
        title = song['title']
        try:
            with ytdl:
                info = ytdl.extract_info(m_url, download=False)
                url = info['url']
                uploader = info.get('uploader', 'Unknown uploader')
                uploader_url = f"https://www.youtube.com/channel/{info['channel_id']}" if 'channel_id' in info else 'Unknown'
                thumbnail = info.get('thumbnail', '')
                duration = info.get('duration', 0)  # Duration in seconds

            voice_client = ctx_or_interaction.guild.voice_client

            def after_playing(error):
                if error:
                    print(f'Error occurred: {error}')
                coro = play_next(ctx_or_interaction)
                fut = asyncio.run_coroutine_threadsafe(coro, bot.loop)
                try:
                    fut.result()
                except:
                    pass

            voice_client.play(discord.FFmpegPCMAudio(url, **get_ffmpeg_options(volume)), after=after_playing)
            current_song = m_url
            current_song_info = {
                "title": title,
                "uploader": uploader,
                "uploader_url": uploader_url,
                "thumbnail": thumbnail,
                "url": m_url,
                "duration": duration
            }

            embed = discord.Embed(title="正在播放", description=f"[{title}]({m_url})", color=discord.Color.blue())
            embed.set_thumbnail(url=thumbnail)
            embed.add_field(name="上傳者", value=f"[{uploader}]({uploader_url})", inline=True)
            embed.add_field(name="長度", value=f"{duration // 60}:{duration % 60:02d}", inline=True)
            embed.add_field(name="音量", value=f"{volume:.1f}", inline=True)
            view = NowPlayingView()

            if isinstance(ctx_or_interaction, discord.Interaction):
                await ctx_or_interaction.response.edit_message(embed=embed, view=view)
            else:
                await ctx_or_interaction.send(embed=embed, view=view)
        except Exception as e:
            print(f"播放歌曲時錯誤: {e}")
    else:
        is_playing = False
        if isinstance(ctx_or_interaction, discord.Interaction):
            await ctx_or_interaction.response.edit_message(content='No more songs in the queue.')
        else:
            await ctx_or_interaction.send('No more songs in the queue.')
        current_song = None
        current_song_info = {}

@bot.command(name='join', help='Joins a voice channel')
async def join(ctx):
    if ctx.voice_client is not None:
        return await ctx.voice_client.move_to(ctx.author.voice.channel)

    if not ctx.author.voice:
        await ctx.send(f"{ctx.author.name} is not connected to a voice channel")
        return

    await ctx.author.voice.channel.connect()

@bot.command(name='leave', help='Leaves the voice channel')
async def leave(ctx):
    voice_client = ctx.message.guild.voice_client
    if voice_client and voice_client.is_connected():
        await voice_client.disconnect()
        await ctx.send("Disconnected from the voice channel.")
    else:
        await ctx.send("The bot is not connected to a voice channel.")

@bot.command(name='now', help='Shows the currently playing song')
async def now_playing(ctx):
    if current_song_info:
        duration = current_song_info.get('duration', 0)
        embed = discord.Embed(title="正在播放", description=f"[{current_song_info['title']}]({current_song_info['url']})", color=discord.Color.blue())
        embed.set_thumbnail(url=current_song_info['thumbnail'])
        embed.add_field(name="上傳者", value=f"[{current_song_info['uploader']}]({current_song_info['uploader_url']})", inline=True)
        embed.add_field(name="長度", value=f"{duration // 60}:{duration % 60:02d}", inline=True)
        embed.add_field(name="音量", value=f"{volume:.1f}", inline=True)
        view = NowPlayingView()
        await ctx.send(embed=embed, view=view)
    else:
        await ctx.send('No song is currently playing.')

@bot.command(name='loop', help='重複播放當前歌曲')
async def toggle_loop(ctx):
    global loop
    loop = not loop
    await ctx.send(f'重複播放 is {"enabled" if loop else "disabled"}')

@bot.command(name='repeat', help='整個播放清單循環播放')
async def toggle_repeat(ctx):
    global repeat
    repeat = not repeat
    await ctx.send(f'循環播放 is {"enabled" if repeat else "disabled"}')

@bot.command(name='shuffle', help='重新排序播放清單')
async def shuffle(ctx):
    random.shuffle(queue)
    await ctx.send('重新排序播放清單')

@bot.command(name='play', help='Plays a song or playlist from YouTube or Spotify')
async def play(ctx, *, url: str = None):
    global is_playing

    voice_client = ctx.message.guild.voice_client

    if not voice_client or not voice_client.is_connected():
        await ctx.send("The bot is not connected to a voice channel. Use the `!join` command to summon the bot to your channel.")
        return

    if url is None:
        await ctx.send('You need to provide a YouTube or Spotify URL to play a song or playlist.')
        return

    loading_message = await ctx.send('Loading your music...')

    if 'spotify.com' in url:
        # Extract track ID from Spotify URL
        track_id = url.split('/')[-1].split('?')[0]
        try:
            track = spotify_client.track(track_id)
            query = f"{track['name']} {track['artists'][0]['name']}"
            # Search for the track on YouTube
            info = ytdl.extract_info(f"ytsearch:{query}", download=False)['entries'][0]
            url = info['webpage_url']
        except Exception as e:
            await ctx.send(f"An error occurred while processing the Spotify URL: {e}")
            return

    try:
        with ytdl:
            info = ytdl.extract_info(url, download=False)
            if 'entries' in info:
                # This is a playlist
                for entry in info['entries']:
                    queue.append({'url': entry['webpage_url'], 'title': entry.get('title', 'Unknown title')})
                await ctx.send(f'Added {len(info["entries"])} songs from the playlist to the queue.')
            else:
                # This is a single video
                queue.append({'url': url, 'title': info.get('title', 'Unknown title')})
                await ctx.send(f'Added to queue: {info.get("title", "Unknown title")}')
    except Exception as e:
        await ctx.send(f"An error occurred while trying to play the song or playlist: {e}")

    if not is_playing:
        await play_next(ctx)

    await loading_message.delete()

@bot.command(name='skip', help='跳過目前歌曲')
async def skip(ctx):
    voice_client = ctx.message.guild.voice_client

    if voice_client.is_playing():
        voice_client.stop()
        await ctx.send('跳過目前...')
    else:
        await ctx.send('沒有歌曲正在播放')

class QueueView(View):
    def __init__(self, queue, current_page=0):
        super().__init__(timeout=3600.0)  # Set the timeout to 1 hour
        self.queue = queue
        self.current_page = current_page
        self.message = None
        self.interaction = None

    @discord.ui.button(label="⬅️", style=discord.ButtonStyle.primary, custom_id="prev_button")
    async def prev_button(self, interaction: discord.Interaction, button: Button):
        if self.current_page > 0:
            self.current_page -= 1
            await self.update_message(interaction)

    @discord.ui.button(label="➡️", style=discord.ButtonStyle.primary, custom_id="next_button")
    async def next_button(self, interaction: discord.Interaction, button: Button):
        if (self.current_page + 1) * 10 < len(self.queue):
            self.current_page += 1
            await self.update_message(interaction)

    @discord.ui.button(label="🔁 Repeat", style=discord.ButtonStyle.secondary, custom_id="repeat_button")
    async def repeat_button(self, interaction: discord.Interaction, button: Button):
        global repeat
        repeat = not repeat
        repeat_status = "enabled" if repeat else "disabled"
        await interaction.response.edit_message(content=f'Repeat is now {repeat_status}.', view=self)
        await self.update_message(interaction)

    def update_buttons(self):
        self.children[0].disabled = self.current_page == 0
        self.children[1].disabled = (self.current_page + 1) * 10 >= len(self.queue)

    async def update_message(self, interaction):
        self.interaction = interaction
        self.update_buttons()
        embed = get_queue_embed(self.current_page, self.queue)
        await interaction.response.edit_message(embed=embed, view=self)

def get_queue_embed(page, queue):
    embed = discord.Embed(title="當前播放清單", color=discord.Color.green())
    start = page * 10
    end = min((page + 1) * 10, len(queue))
    for index in range(start, end):
        song = queue[index]
        song_details = f"[{song['title']}]({song['url']})"
        embed.add_field(name=f"#{index + 1}", value=song_details, inline=False)
    repeat_status = "enabled" if repeat else "disabled"
    embed.set_footer(text=f"Page {page + 1}/{(len(queue) - 1) // 10 + 1} • Repeat is {repeat_status}")
    return embed

async def update_queue_message():
    if hasattr(bot, 'queue_message') and bot.queue_message:
        view = bot.queue_view
        view.queue = queue
        if view.interaction:
            await view.update_message(view.interaction)

@bot.command(name='queue', help='顯示播放清單內的歌曲')
async def show_queue(ctx):
    if len(queue) == 0:
        await ctx.send('播放清單是空的')
        return

    current_page = 0
    embed = get_queue_embed(current_page, queue)
    message = await ctx.send(embed=embed)
    view = QueueView(queue, current_page)
    view.message = message
    await message.edit(view=view)
    bot.queue_message = message
    bot.queue_view = view

@bot.command(name='clear', help='停止播放歌曲並清空播放清單')
async def clear(ctx):
    global queue, is_playing

    global repeat
    repeat = False
    queue = []
    is_playing = False
    voice_client = ctx.message.guild.voice_client

    if voice_client.is_playing() or voice_client.is_paused():
        voice_client.stop()
        await ctx.send('停止播放歌曲並清空播放清單')
        repeat = True
    else:
        await ctx.send('沒有歌曲正在播放')

@bot.command(name='remove', help='Removes a song from the queue by its position')
async def remove(ctx, position: int):
    if 0 < position <= len(queue):
        removed_song = queue.pop(position - 1)
        await ctx.send(f'Removed: {removed_song["title"]} from the queue.')
    else:
        await ctx.send('Invalid position.')

@bot.command(name='move', help='Moves a song to a new position in the queue')
async def move(ctx, current_position: int, new_position: int):
    if 0 < current_position <= len(queue) and 0 < new_position <= len(queue):
        song = queue.pop(current_position - 1)
        queue.insert(new_position - 1, song)
        await ctx.send(f'Moved song from position {current_position} to {new_position}.')
    else:
        await ctx.send('Invalid positions.')

@bot.command(name='pause', help='Pauses the current song')
async def pause(ctx):
    voice_client = ctx.message.guild.voice_client

    if voice_client.is_playing():
        voice_client.pause()
        await ctx.send('Paused the current song.')
    else:
        await ctx.send('No song is currently playing.')

@bot.command(name='resume', help='Resumes the current song')
async def resume(ctx):
    voice_client = ctx.message.guild.voice_client

    if voice_client.is_paused():
        voice_client.resume()
        await ctx.send('Resumed the current song.')
    else:
        await ctx.send('No song is currently paused.')
    
# Search command
@bot.command(name='search', help='Searches for a song on YouTube')
async def search(ctx, *, query: str):
    await ctx.send(f'Searching for: {query}...')

    ytdl = YoutubeDL({'format': 'bestaudio', 'quiet': True})
    try:
        search_results = ytdl.extract_info(f"ytsearch5:{query}", download=False)['entries']

        if not search_results:
            await ctx.send('No results found.')
            return

        search_view = SearchView(search_results)
        embed = get_search_embed(search_results)
        message = await ctx.send(embed=embed, view=search_view)
        search_view.message = message

    except Exception as e:
        await ctx.send(f'An error occurred while searching: {e}')

def get_search_embed(search_results):
    embed = discord.Embed(title="Search Results", color=discord.Color.blue())
    for index, result in enumerate(search_results):
        title = result.get('title', 'Unknown title')
        url = result.get('webpage_url', '')
        uploader = result.get('uploader', 'Unknown uploader')
        embed.add_field(name=f"{index + 1}. {title}", value=f"[Link]({url})\nUploader: {uploader}", inline=False)
    return embed

class SearchView(View):
    def __init__(self, search_results):
        super().__init__(timeout=60.0)
        self.search_results = search_results
        self.message = None

        # Create select options based on search results
        options = [
            discord.SelectOption(label=f"{i + 1}. {result.get('title', 'Unknown title')}", value=str(i))
            for i, result in enumerate(self.search_results)
        ]

        # Add select dropdown to the view
        self.add_item(discord.ui.Select(placeholder='Select a song to play', min_values=1, max_values=1, options=options, custom_id='select_search_result'))

    async def interaction_check(self, interaction: discord.Interaction):
        # Ensure only the user who invoked the search can interact with the buttons and select menu
        return interaction.user == self.message.author

#local save play_list

PLAYLIST_FILE = 'playlists.json'
creating_playlist = False  # Track whether we are in "listening mode"
temporary_playlist = []    # Temporary list for the current playlist creation

def load_playlists() -> dict:
    """Loads playlists from the JSON file."""
    if not os.path.exists(PLAYLIST_FILE):
        return {}
    with open(PLAYLIST_FILE, 'r') as file:
        return json.load(file)

def save_playlists(playlists: dict) -> None:
    """Saves playlists to the JSON file."""
    with open(PLAYLIST_FILE, 'w') as file:
        json.dump(playlists, file, indent=4)

@bot.command(name='list', help='列出已儲存的播放清單')
async def list_playlists(ctx):
    playlists = load_playlists()
    user_id = str(ctx.author.id)

    if user_id not in playlists or not playlists[user_id]:
        await ctx.send("你沒有儲存的播放清單.")
        return

    playlist_names = list(playlists[user_id].keys())
    embed = discord.Embed(title="你的播放清單列", color=discord.Color.blue())

    # Display playlists in the embed
    for index, name in enumerate(playlist_names, 1):
        embed.add_field(name=f"{index}. {name}", value=f"`!load {name}` 或 `!delete {name}`", inline=False)

    await ctx.send(embed=embed)

@bot.command(name='create', help='創建永久歌單')
async def create_playlist(ctx, playlist_name: str):
    global creating_playlist, temporary_playlist

    # Initialize the temporary playlist and set creating mode to True
    temporary_playlist.clear()
    creating_playlist = True

    await ctx.send(
        f"創建播放清單，名稱: '{playlist_name}'~"
        f"請開始加入歌曲。直接輸入連結即可, `end` 來完成創建~"
    )

    def check_message(msg):
        # Check if the message is from the same user and in the same channel
        return msg.author == ctx.author and msg.channel == ctx.channel

    # Enter the "listening mode"
    while creating_playlist:
        try:
            msg = await bot.wait_for('message', check=check_message, timeout=300.0)  # Wait up to 5 minutes
            if msg.content == 'end':
                await finish_creation(ctx, playlist_name)
                return
            else:
                await add_url_to_temp(ctx, msg.content)

        except asyncio.TimeoutError:
            creating_playlist = False
            await ctx.send("播放清單創建超時。 使用 `!create 名稱` 重新開始!")

class PlaylistView(View):
    def __init__(self, playlist, playlist_name, current_page=0):
        super().__init__(timeout=3600.0)  # 1-hour timeout
        self.playlist = playlist
        self.playlist_name = playlist_name
        self.current_page = current_page
        self.message = None
        self.interaction = None

    @discord.ui.button(label="⬅️", style=discord.ButtonStyle.primary)
    async def prev_button(self, interaction: discord.Interaction, button: Button):
        if self.current_page > 0:
            self.current_page -= 1
            await self.update_message(interaction)

    @discord.ui.button(label="➡️", style=discord.ButtonStyle.primary)
    async def next_button(self, interaction: discord.Interaction, button: Button):
        if (self.current_page + 1) * 10 < len(self.playlist):
            self.current_page += 1
            await self.update_message(interaction)

    def update_buttons(self):
        self.children[0].disabled = self.current_page == 0
        self.children[1].disabled = (self.current_page + 1) * 10 >= len(self.playlist)

    async def update_message(self, interaction):
        self.interaction = interaction
        self.update_buttons()
        embed = get_playlist_embed(self.playlist_name, self.current_page, self.playlist)
        await interaction.response.edit_message(embed=embed, view=self)

def get_playlist_embed(playlist_name, page, playlist):
    embed = discord.Embed(title=f"播放清單: {playlist_name}", color=discord.Color.green())
    start = page * 10
    end = min((page + 1) * 10, len(playlist))
    for index in range(start, end):
        song = playlist[index]
        embed.add_field(name=f"{index + 1}. {song['title']}", value=song['url'], inline=False)
    total_pages = (len(playlist) - 1) // 10 + 1
    embed.set_footer(text=f"第 {page + 1} 頁，共 {total_pages} 頁")
    return embed

@bot.command(name='show', help='顯示已儲存播放清單中得所有歌曲')
async def show_playlist(ctx, playlist_name: str):
    playlists = load_playlists()
    user_id = str(ctx.author.id)

    if user_id not in playlists or playlist_name not in playlists[user_id]:
        await ctx.send(f"沒有找到播放清單 '{playlist_name}'。")
        return

    playlist = playlists[user_id][playlist_name]
    if not playlist:
        await ctx.send(f"播放清單 '{playlist_name}' 是空的。")
        return

    current_page = 0
    embed = get_playlist_embed(playlist_name, current_page, playlist)
    message = await ctx.send(embed=embed)
    view = PlaylistView(playlist, playlist_name, current_page)
    view.message = message
    await message.edit(view=view)

@bot.command(name='playlist_remove', help='在已儲存得播放清單刪除歌曲: `!playlist_remove 清單名 位置`')
async def playlist_remove(ctx, playlist_name: str, position: int):
    playlists = load_playlists()
    user_id = str(ctx.author.id)

    if user_id not in playlists or playlist_name not in playlists[user_id]:
        await ctx.send(f"沒有找到播放清單 '{playlist_name}'。")
        return

    playlist = playlists[user_id][playlist_name]
    if 0 < position <= len(playlist):
        removed_song = playlist.pop(position - 1)
        save_playlists(playlists)
        await ctx.send(f"已從播放清單 '{playlist_name}' 中移除 '{removed_song['title']}'。")
    else:
        await ctx.send("無效的位置。")

@bot.command(name='playlist_move', help='在已儲存得播放清單更改歌曲位置: `!laylist_move 清單名 位置 位置`')
async def playlist_move(ctx, playlist_name: str, current_position: int, new_position: int):
    playlists = load_playlists()
    user_id = str(ctx.author.id)

    if user_id not in playlists or playlist_name not in playlists[user_id]:
        await ctx.send(f"沒有找到播放清單 '{playlist_name}'。")
        return

    playlist = playlists[user_id][playlist_name]
    if 0 < current_position <= len(playlist) and 0 < new_position <= len(playlist):
        song = playlist.pop(current_position - 1)
        playlist.insert(new_position - 1, song)
        save_playlists(playlists)
        await ctx.send(f"已在播放清單 '{playlist_name}' 中將歌曲從位置 {current_position} 移動到 {new_position}。")
    else:
        await ctx.send("無效的位置。")

@bot.command(name='playlist_add', help='在已儲存得播放清單加入歌曲: `!playlist_add 清單名 連結`')
async def playlist_add(ctx, playlist_name: str, *, url: str):
    playlists = load_playlists()
    user_id = str(ctx.author.id)

    if user_id not in playlists or playlist_name not in playlists[user_id]:
        await ctx.send(f"沒有找到播放清單 '{playlist_name}'。")
        return

    playlist = playlists[user_id][playlist_name]

    try:
        with yt_dlp.YoutubeDL(ytdl_format_options) as ytdl:
            info = ytdl.extract_info(url, download=False)
            if 'entries' in info:
                await ctx.send("此指令只能添加單首歌曲。")
                return
            else:
                title = info.get('title', '未知的標題')
                song_url = info.get('webpage_url')
                song_entry = {'url': song_url, 'title': title}

                playlist.append(song_entry)
                save_playlists(playlists)
                await ctx.send(f"已將 '{title}' 添加到播放清單 '{playlist_name}' 的末尾。")
    except Exception as e:
        await ctx.send(f"添加歌曲時發生錯誤: {e}")

@bot.command(name='playlist_insert', help='在已儲存得播放清單插入歌曲: `!playlist_insert 清單名 位置 連結`')
async def playlist_insert(ctx, playlist_name: str, position: int, *, url: str):
    playlists = load_playlists()
    user_id = str(ctx.author.id)

    if user_id not in playlists or playlist_name not in playlists[user_id]:
        await ctx.send(f"沒有找到播放清單 '{playlist_name}'。")
        return

    playlist = playlists[user_id][playlist_name]

    if position < 1 or position > len(playlist) + 1:
        await ctx.send(f"無效的位置。位置必須在 1 到 {len(playlist) + 1} 之間。")
        return

    try:
        with yt_dlp.YoutubeDL(ytdl_format_options) as ytdl:
            info = ytdl.extract_info(url, download=False)
            if 'entries' in info:
                await ctx.send("此指令只能插入單首歌曲。")
                return
            else:
                title = info.get('title', '未知的標題')
                song_url = info.get('webpage_url')
                song_entry = {'url': song_url, 'title': title}

                playlist.insert(position - 1, song_entry)
                save_playlists(playlists)
                await ctx.send(f"已在播放清單 '{playlist_name}' 的位置 {position} 插入 '{title}'。")
                save_playlists(playlists)
    except Exception as e:
        await ctx.send(f"添加歌曲時發生錯誤: {e}")

async def add_url_to_temp(ctx, url: str):
    """Adds songs from the URL (single or playlist) to the temporary playlist."""
    global temporary_playlist

    try:
        with ytdl:
            info = ytdl.extract_info(url, download=False)

            if 'entries' in info:  # It's a playlist
                entries = info['entries']
                for entry in entries:
                    title = entry.get('title', 'Unknown title')
                    song_url = entry.get('webpage_url')
                    temporary_playlist.append({'url': song_url, 'title': title})

                await ctx.send(f"新增 {len(entries)} 歌曲至播放清單")

            else:  # It's a single song
                title = info.get('title', 'Unknown title')
                song_url = info.get('webpage_url')
                temporary_playlist.append({'url': song_url, 'title': title})

                await ctx.send(f"新增 '{title}' 至播放清單")

    except Exception as e:
        await ctx.send(f"加入歌曲錯誤: {e}")

async def finish_creation(ctx, playlist_name: str):
    """Finishes creating the playlist and saves it."""
    global creating_playlist, temporary_playlist

    if not temporary_playlist:
        await ctx.send("播放清單是空的")
        creating_playlist = False
        return

    playlists = load_playlists()
    user_id = str(ctx.author.id)

    if user_id not in playlists:
        playlists[user_id] = {}

    playlists[user_id][playlist_name] = temporary_playlist.copy()
    save_playlists(playlists)

    await ctx.send(f"播放清單: '{playlist_name}' 加入 {len(temporary_playlist)} 首歌曲, 並完成創建~")
    temporary_playlist.clear()
    creating_playlist = False

@bot.command(name='load', help='載入以儲存的播放清單: `!load 清單名`')
async def load_playlist(ctx, playlist_name: str):
    playlists = load_playlists()
    user_id = str(ctx.author.id)

    if user_id not in playlists or playlist_name not in playlists[user_id]:
        await ctx.send(f"沒有播放清單叫 '{playlist_name}'.")
        return

    global queue, is_playing

    # Add the loaded playlist to the queue
    queue.extend(playlists[user_id][playlist_name])
    await ctx.send(f"從 '{playlist_name}' 加入 {len(playlists[user_id][playlist_name])} songs.")

    # Check if the bot is currently playing music
    voice_client = ctx.guild.voice_client

    if not is_playing and voice_client and not voice_client.is_playing():
        await play_next(ctx)  # Start playing the first song in the loaded queue

@bot.command(name='delete', help='刪除已儲存的播放清單: `!delete 清單名`')
async def delete_playlist(ctx, playlist_name: str):
    playlists = load_playlists()
    user_id = str(ctx.author.id)

    if user_id not in playlists or playlist_name not in playlists[user_id]:
        await ctx.send(f"沒有以儲存得播放清單叫 '{playlist_name}'.")
        return

    # Delete the specified playlist
    del playlists[user_id][playlist_name]
    save_playlists(playlists)

    await ctx.send(f"已刪除 '{playlist_name}'")

#local save play_list

def main() -> None:
    bot.run(TOKEN)

if __name__ == "__main__":
    main()
