import os
from random import randint
import discord

from dotenv import load_dotenv

load_dotenv()

bot = discord.Bot()

def roll_die(num: int):
    return randint(1, num)

DICE = ["d4", "d6", "d8", "d10", "d12", "d20"]

def multiroll(die: str, count: int):
    if die not in DICE:
        return None
    sides = int(die[1:])
    rolls = [randint(1, sides) for _ in range(count)]
    return rolls, sum(rolls) 

@bot.event
async def on_ready():
    print(f"{bot.user} has successfully connected to the cord!")

def make_roll_command(sides: int):
    async def roll(ctx: discord.ApplicationContext):
        await ctx.respond(str(roll_die(sides)))
    return roll

for sides in (4, 6, 8, 10, 12, 20):
    bot.slash_command(name=f"d{sides}", description=f"Roll a d{sides}")(make_roll_command(sides))

@bot.slash_command(name="multiroll", description="Roll a die of your choice but multi")
async def goBig(
    ctx: discord.ApplicationContext, 
    die: discord.Option(str, "die of your choice", autocomplete=discord.utils.basic_autocomplete(DICE)), 
    amount: discord.Option(int, "How many times?", min_value=1, max_value=100)
):
    result = multiroll(die, amount)
    if result is None:
        await ctx.respond(f"I don't know '{die}'. Try one of: {', '.join(DICE)}", ephemeral=True)
        return

    rolls, total = result
    await ctx.respond(f"{', '.join(map(str, rolls))}\ntotal: {total}")

bot.run(os.getenv('DISCORD_TOKEN'))
