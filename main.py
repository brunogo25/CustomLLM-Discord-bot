from datetime import datetime
import io
import json
import urllib
import urllib.request
import discord
from docx import Document
from openai import OpenAI
from configparser import ConfigParser
from discord import app_commands
from discord.channel import TextChannel
from discord.errors import HTTPException

config_file = "config.ini"
config = ConfigParser(interpolation=None)
config.read(config_file)

SERVER_ID = config["discord"]["server_id"]
DISCORD_API_KEY = config["discord"][str("api_key")]
OPENAI_ORG = config["openai"][str("organization")]
OPENAI_API_KEY = config["openai"][str("api_key")]

GUILD = discord.Object(id=SERVER_ID)

SYSTEM_MESSAGE = config["bot"]["system_message"]
HISTORY_LENGTH = config["bot"]["history_length"]

FILE_PATH = config["settings"][str("file_path")]
FILE_NAME_FORMAT = config["settings"][str("file_name_format")]

tools = [
    {
        "type": "function",
        "function": {
            "name": "generate_image_with_dalle",
            "description": "generates an image using Dall-E and returns it as a URL",
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": "prompt for the image to be generated"
                    },
                    "style": {
                        "type": "string",
                        "description": "the style for the image. Options: natural, vivid"
                    }
                },
                "required": ["prompt"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_text_file",
            "description": "Creates and returns a text file with the provided content. Used for documents, texts or code/script files",
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "The text content for the file."
                    },
                    "file_type": {
                        "type": "string",
                        "description": "file extension to use. Example: .py, .docx, .txt"
                    }
                },
                "required": ["prompt", "file_type"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_voice_message",
            "description": "creates and returns a voice message with the prompt as text",
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": "what the voice message should say"
                    },
                    "voice": {
                        "type": "string",
                        "description": "The voice to use. Pick one, ordered from high to low pitch. Male voices: fable, echo, onyx - Female voices: nova, shimmer, alloy"
                    }
                },
                "required": ["prompt", "voice"],
            },
        },
    }
]

ai = OpenAI(api_key=OPENAI_API_KEY)


def download_image(url: str):
    file_name = f"{datetime.now().strftime(FILE_NAME_FORMAT)}.jpg"
    full_path = f"{FILE_PATH}{file_name}"
    urllib.request.urlretrieve(url, full_path)
    return file_name


def create_voice_message(prompt, voice):
    print("Creating Voice Message with prompt: " + prompt)
    response = ai.audio.speech.create(
        model="tts-1-hd",
        voice=voice,
        input=prompt
    )

    file_name = f"{datetime.now().strftime(FILE_NAME_FORMAT)}.mp3"
    full_path = f"{FILE_PATH}{file_name}"

    response.stream_to_file(full_path)

    return full_path


def create_text_file(content, file_type):

    if content is None or file_type is None:
        return

    if file_type[0] is not ".":
        file_type = "." + file_type

    response = ai.chat.completions.create(
        model="gpt-4-0125-preview",
        messages=[
            {"role": "system", "content": "The user gives you a text. Format and return that text for a " + file_type + " file. Do not generate or return anything else."},
            {"role": "user", "content": content}
        ],
        max_tokens=4096
    ).choices[0].message.content

    text = response
    file_name = f"{datetime.now().strftime(FILE_NAME_FORMAT)}" + file_type
    full_path = f"{FILE_PATH}{file_name}"

    if file_type == ".docx":
        document = Document()
        document.add_paragraph(text)
        document.save(full_path)
    else:
        with open(full_path, "w") as file:
            file.write(text)

    return full_path


def trim_conversation_history(history, max_length=int(HISTORY_LENGTH)):
    if len(history) > max_length:
        history = history[-max_length:]
    return history


def generate_image_with_dalle(prompt, style):
    print("Creating image with prompt: " + prompt)
    response = ai.images.generate(
        prompt = prompt,
        model = "dall-e-3",
        quality="hd",
        response_format="url"
    )
    image_data = response.data[0]
    image_url = image_data.url

    print(image_url)
    return "files/" + download_image(image_url)


class Client(discord.Client):
    def __init__(self, *, intents: discord.Intents):
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self.conversation_history = []

    async def setup_hook(self):
        self.tree.copy_global_to(guild=GUILD)
        await self.tree.sync(guild=GUILD)

    async def on_message(self, message):
        
        author = message.author
        embed_files = []
        if message.author == self.user or not message.guild:
            return
        
        response_channel = None  

   
        if isinstance(message.channel, discord.TextChannel):
            try:
                acknowledgement_thread = await message.channel.create_thread(name=f"Response to {message.author.display_name}", message=message, auto_archive_duration=60)
                response_channel = acknowledgement_thread
            except HTTPException as e:
                print(f"Failed to create a thread due to an error: {e}")
        elif isinstance(message.channel, discord.Thread):        
            response_channel = message.channel

    
        if not response_channel:
            print("No response channel determined. Unable to proceed.")
            return
    
        await response_channel.send("Mike-s Thinking...")

        input_content = message.content
        print(f"{message.author}: {input_content}")

        self.conversation_history.append({"role": "system", "content": f"The user is {author.display_name}. {SYSTEM_MESSAGE}"})

        for attachment in message.attachments:
            if attachment.filename.endswith(('.py', ".txt", ".java", ".rb", ".bas", ".html", ".php", ".js", ".md", ".info", ".csv", ".cs")):
                file_content = io.BytesIO()
                await attachment.save(file_content)
                file_content.seek(0)
                text_data = file_content.read().decode('utf-8')
                self.conversation_history.append({"role": "system", "content": "The user has sent you a file with his request. The file name is " + attachment.filename + ". Here are its contents: " + text_data})
            elif attachment.filename.endswith((".png", ".jpeg", ".jpg")):
                image_url = attachment.proxy_url
                response = ai.chat.completions.create(
                    model="gpt-4-vision-preview",
                    messages=[
                        {
                            "role": "user",
                            "content": [
                                {"type": "text",
                                 "text": "Describe the following image"},
                                {
                                    "type": "image_url",
                                    "image_url": {
                                        "url": image_url,
                                    },
                                },
                            ],
                        }
                    ], max_tokens=4096,
                ).choices[0].message.content
                self.conversation_history.append({"role": "system", "content": "The user has sent you an image with his request. You are able to see the image, don't tell the user that you can't see the image. Use the following description of that image to help the user: " + response})
        self.conversation_history.append({"role": "user", "content": input_content})
        self.conversation_history = trim_conversation_history(self.conversation_history)

        try:
            response = ai.chat.completions.create(
                model="gpt-4-0125-preview",
                messages=self.conversation_history,
                tools=tools,
                tool_choice="auto",
                max_tokens=4096
            )

            assistant_response = response.choices[0].message
            tool_calls = assistant_response.tool_calls

            if tool_calls:
                available_functions = {
                    "generate_image_with_dalle": generate_image_with_dalle,
                    "create_text_file": create_text_file,
                    "create_voice_message": create_voice_message,
                }
                for tool_call in tool_calls:
                    if assistant_response.content is None:
                        assistant_response.content = ""
                    self.conversation_history.append({"role": "assistant", "content": assistant_response.content})
                    function_name = tool_call.function.name
                    function_to_call = available_functions[function_name]
                    function_args = json.loads(tool_call.function.arguments)
                    if function_name in available_functions.keys():
                        if function_name == "create_text_file":
                            function_response = function_to_call(
                                content=function_args.get("content"),
                                file_type=function_args.get("file_type")
                            )
                        elif function_name == "create_voice_message":
                            function_response = function_to_call(
                                prompt=function_args.get("prompt"),
                                voice=function_args.get("voice")
                            )
                        else:
                            function_response = function_to_call(
                                prompt=function_args.get("prompt"),
                                style=function_args.get("style")
                            )
                        self.conversation_history.append(
                            {"role": "system",
                             "content": "The file has been created and is attached to your next message"
                             }
                        )
                        if function_response is not None:
                            embed_files.append(discord.File(function_response))

                response = ai.chat.completions.create(
                    model="gpt-4-0125-preview",
                    messages=self.conversation_history,
                    max_tokens=4096
                )
                assistant_response = response.choices[0].message
            else:
                self.conversation_history.append({"role": "assistant", "content": assistant_response.content})
            assistant_response = assistant_response.content

        except AttributeError:
            assistant_response = "It looks like you might have to update your openai package. You can do that with ```pip install --upgrade openai```"
        except ImportError:
            assistant_response = "You might not have all required packages installed. Make sure you install the openai and discord package"




        if assistant_response is not None:
            parts = [assistant_response[i:i + 2000] for i in range(0, len(assistant_response), 2000)]
        for part in parts:
            await response_channel.send(content=part, files=embed_files if len(embed_files) > 0 else None)



intents = discord.Intents.default()
intents.messages = True
intents.message_content = True
client = Client(intents=intents)

client.run(DISCORD_API_KEY)