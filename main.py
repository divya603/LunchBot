import os
import json
import discord
from dotenv import load_dotenv
from pathlib import Path
import re
import requests
from rapidfuzz import fuzz, process
import asyncio
import openai
from playwright.async_api import async_playwright
import pdfplumber

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "0"))  # optional for channel restriction

intents = discord.Intents.default()
intents.message_content = True

client = discord.Client(intents=intents)

#open ai key
openai.api_key = os.getenv("OPENAI_API_KEY")
print("OPENAI_API_KEY:", os.getenv("OPENAI_API_KEY"))
orders = {}
menu_items = []
last_restaurant_results = []
selected_restaurant = None

# Load menu from JSON
menu_path = Path("menu.json")
if menu_path.exists():
    with open(menu_path, 'r', encoding='utf-8') as f:
        menu_items = json.load(f)
else:
    print("⚠️ menu.json not found. Please add it to your project folder.")

@client.event
async def on_ready():
    print(f'✅ Bot is online as {client.user}')

def search_restaurants(cuisine, location="Newark, NJ"):
    api_key = os.getenv("SERPAPI_KEY")  
    params = {
        "engine": "google_maps",
        "q": f"{cuisine} restaurants in {location}",
        "api_key": api_key
    }
    res = requests.get("https://serpapi.com/search", params=params)
    data = res.json()
    #print("SERPAPI raw response:", data) 

    if "error" in data:
        print("SERPAPI ERROR:", data["error"])
        return [f"❌ SERPAPI error: {data['error']}"]

    results = []
    for place in data.get("local_results", [])[:5]:
        name = place.get("title")
        address = place.get("address")
        place_id = place.get("place_id")
        link = f"https://www.google.com/maps/place/?q=place_id:{place_id}" if place_id else None
        website = place.get("website")
        if name and address and link:
            results.append({
                "title": name,
                "address": address,
                "link": link,
                "website": website
            })
    if not results:
        # For debugging, return the raw response (truncated if too long)
        return [f"❌ Couldn't find restaurants. Raw response: {str(data)[:1000]}"]
    return results

async def fetch_menu_from_website(url: str) -> str:
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page()
        await page.goto(url)
        text = await page.locator("body").inner_text()
        await browser.close()
        return text

def extract_menu_with_gpt(raw_text: str) -> list:
    prompt = (
        "Extract this menu into JSON list, each with fields 'name','description','price':\n\n"
        f"{raw_text}"
    )
    resp = openai.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[{"role":"system","content":"You extract menu info."},
                  {"role":"user","content":prompt}],
        temperature=0
    )
    content = resp.choices[0].message.content
    return json.loads(content)


@client.event
async def on_message(message):
    global last_restaurant_results, selected_restaurant

    if message.author == client.user:
        return

    if CHANNEL_ID and message.channel.id != CHANNEL_ID:
        return

    content = message.content.lower()
    user = str(message.author.display_name)
    if content.strip() in ["thanks", "thank you"]:
        await message.channel.send("You're welcome!")
        return

    if selected_restaurant is None and last_restaurant_results:
        titles = [r["title"].lower() for r in last_restaurant_results]
        words = set(re.findall(r'\w+', content))
        best_score = 0
        best_idx = None
        for idx, title in enumerate(titles):
            title_words = set(re.findall(r'\w+', title))
            overlap = words & title_words
            score = process.extractOne(content, [title], scorer=fuzz.token_sort_ratio)[1]
            if overlap:
                score += 20
            if score > best_score:
                best_score = score
                best_idx = idx
        if best_score > 80 and best_idx is not None:
            selected_restaurant = last_restaurant_results[best_idx]
            await message.channel.send(f"✅ Got it! You've selected **{selected_restaurant['title']}**.")
            website_url = selected_restaurant.get("website")
            if not website_url:
                await message.channel.send("❌ Sorry, I couldn't find a website for this restaurant.")
                return
            await message.channel.send("🔍 Fetching menu... this may take a moment.")
            menu_text = await fetch_menu_from_website(website_url)
            await message.channel.send("✅ Menu page retrieved. Parsing...")
            parsed = extract_menu_with_gpt(menu_text)
            with open("menu.json", "w", encoding="utf-8") as f:
                json.dump(parsed, f, indent=2)
            menu_items.clear()
            menu_items.extend(parsed)
            await message.channel.send("📥 Menu loaded! Type `!menu` to begin ordering.")
            return

    # Command for help and greeting
    if content.strip() in ["!help", "hi", "hello"]:
        help_message = (
            f"👋 Hi! I'm LunchBot, your lunch and restaurant assistant!\n\n"
            f"You can ask me to order from our menu, or just tell me what kind of food you're in the mood for.\n\n"
            f"**Here's what I can do:**\n"
            f"• Type `!menu` to see all the available options.\n"
            f"• Order by typing the quantity and item name (e.g., `I'll have 2 pork rolls and 1 breakfast sandwich`).\n"
            f"• To see all current orders, type `!summary`.\n"
            f"• To cancel, you can say `cancel 1 pork roll` or just `cancel pork roll` to remove all of them.\n"
            f"• Want to eat out? Just tell me what cuisine you want (like `I want Thai food` or `Show me Italian restaurants`) and I'll find places nearby!\n\n"
            f"**Tip:** For best results, try to use the item names as they appear in the menu when ordering."
        )
        await message.channel.send(help_message)
        return
    #detect cuisine intent
    COMMON_CUISINES = ["italian", "mexican", "thai", "chinese", "indian", "japanese", "greek",
                        "mediterranean", "korean", "vietnamese", "french", "spanish", "lebanese"]
    words = set(re.findall(r'\b\w+\b', content))
    for cuisine in COMMON_CUISINES:
        if cuisine.lower() in words:
            await message.channel.send(f"🍽️ Looking for **{cuisine.title()}** food near you...")
            matches = search_restaurants(cuisine)
            if matches and isinstance(matches[0], dict):
                last_restaurant_results = matches
                msg_list = [f"**{m['title']}**\n📍 {m['address']}\n🔗 <{m['link']}>" for m in matches]
                await message.channel.send("\n\n".join(msg_list))
            else:
                await message.channel.send("❌ Couldn't find restaurants right now.")
            return

    # Command to get the summary
    if content.strip() == "!summary":
        if not orders:
            await message.channel.send("No orders yet!")
            return

        summary_lines = []
        for u, items in orders.items():
            summary = ', '.join([f"{item} x{count}" if count > 1 else item for item, count in items.items()])
            summary_lines.append(f"**{u}**: {summary}")
        await message.channel.send("\n".join(summary_lines))
        return

    # Command to display the menu
    if content.strip() == "!menu":
        if not menu_items:
            await message.channel.send("📜 The menu is currently empty.")
            return

        menu_chunks = []
        current_chunk = ""
        for item in menu_items:
            line = f"**{item['name']}** - ${item['price']:.2f}\n*_{item['description']}_*\n\n"
            if len(current_chunk) + len(line) > 1900:  # Leave a buffer
                menu_chunks.append(current_chunk)
                current_chunk = ""
            current_chunk += line
        
        if current_chunk:
            menu_chunks.append(current_chunk)

        await message.channel.send("**📜 Lunch Menu**")
        for chunk in menu_chunks:
            await message.channel.send(chunk)
        return

    # Command to cancel items from the user's order
    if "cancel" in content or "remove" in content or "forget" in content or "less" in content:
        item_cancelled = False
        if user in orders:
            cancel_matches = {}
            matched_items = set()
            removed_summary = {}
            # Extract all quantity-based cancellations from the message
            qty_pattern = r"(?:cancel|remove|forget|less)\\s+(\\d+)\\s+([a-zA-Z0-9 +&'().-]+)"
            for qty_match in re.finditer(qty_pattern, content):
                qty = int(qty_match.group(1))
                item_part = qty_match.group(2).strip()
                for item in menu_items:
                    item_name = item['name']
                    if item_part in item_name.lower() or item_name.lower() in item_part:
                        cancel_matches[item_name] = qty
                        matched_items.add(item_name)
                        break
            # Then, process non-quantity (remove all) cancellations for items not already matched
            for item in menu_items:
                item_name = item['name']
                if item_name in matched_items:
                    continue
                base_pattern = re.sub(r'\\s+', r'\\s+', re.escape(item_name.lower()))
                plural_pattern = rf"{base_pattern}(?:es|s)?"
                if re.search(plural_pattern, content, re.IGNORECASE):
                    cancel_matches[item_name] = None
            for item_name, qty in cancel_matches.items():
                item_key = item_name
                if item_key in orders[user]:
                    original_qty = orders[user][item_key]
                    if qty is not None:
                        if original_qty > qty:
                            orders[user][item_key] -= qty
                            removed_summary[item_key] = qty
                            item_cancelled = True
                        else:
                            del orders[user][item_key]
                            removed_summary[item_key] = original_qty
                            item_cancelled = True
                    else:
                        del orders[user][item_key]
                        removed_summary[item_key] = original_qty
                        item_cancelled = True
            if item_cancelled:
                if not orders[user]:
                    del orders[user]
                summary_str = ', '.join([f"{item} x{count}" if count > 1 else item for item, count in removed_summary.items()])
                await message.channel.send(f"🗑️ Okay, {user}. I've removed {summary_str} from your order.")
            else:
                await message.channel.send(f"⚠️ No matching item found in {user}'s order to cancel.")
        else:
            await message.channel.send(f"⚠️ No order found for {user} to cancel.")
        return

    # Try to match menu items and quantities (with plural flexibility)
    matched = {}
    for item in menu_items:
        name = item['name'].lower()
        base_pattern = re.sub(r'\\s+', r'\\s+', name)
        plural_pattern = rf"{base_pattern}(?:es|s)?"
        # Look for "1 pork roll"
        full_pattern = rf"(\d+)\s+({plural_pattern})\b"
        matches = re.findall(full_pattern, content)
        if matches:
            for qty_str, _ in matches:
                qty = int(qty_str)
                matched[item['name']] = matched.get(item['name'], 0) + qty
        # Look for "pork roll" (quantity 1)
        elif re.search(rf"\b{plural_pattern}\b", content):
            matched[item['name']] = matched.get(item['name'], 0) + 1

    if matched:
        if user not in orders:
            orders[user] = {}
        for item, qty in matched.items():
            orders[user][item] = orders[user].get(item, 0) + qty
        summary = ', '.join([f"{item} x{qty}" if qty > 1 else item for item, qty in matched.items()])
        await message.channel.send(f"✅ Got it, {user}! Added {summary} to your order.")
        return

    # If we reach here, no command was recognized and no order was placed.
    await message.channel.send(
        "Sorry, I didn't quite understand that. "
        "I can take your order (e.g., `1 pork roll`), or you can use one of my commands: `!menu` to view the menu, `!summary` to see all current orders, or `!help` for more information."
    )

client.run(TOKEN)
