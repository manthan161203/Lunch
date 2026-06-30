import csv
import datetime
import hashlib
import json
import os
from neonize.utils.jid import Jid2String
import requests

import config

def initialize_storage():
    """
    Initializes storage files (orders.csv and summary.csv) if they do not exist.
    """
    if not os.path.exists(config.ORDERS_CSV_FILE):
        with open(config.ORDERS_CSV_FILE, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["Date", "Time", "Person", "Vendor", "Selected Price (₹)"])
        print(f"Initialized new file: {config.ORDERS_CSV_FILE}")

    if not os.path.exists(config.SUMMARY_CSV_FILE):
        with open(config.SUMMARY_CSV_FILE, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["Person", "Total Orders", "Total Amount (₹)", "Breakdown"])
        print(f"Initialized new file: {config.SUMMARY_CSV_FILE}")


def get_vendor_for_poll(poll_name: str) -> str:
    """
    Matches the poll name against today's active menus to find the vendor name.
    If no match is found, defaults to the first word of the poll name.
    """
    # 1. Split first word as a default candidate
    words = poll_name.strip().split()
    default_vendor = words[0] if words else poll_name
    
    # 2. Try to match against today's active vendors in config.TODAYS_MENUS
    try:
        now_dt = datetime.datetime.now(config.TZ)
        date_str = now_dt.strftime("%Y-%m-%d")
        
        # Get list of vendors who posted menus today
        today_vendors = set()
        for item in config.TODAYS_MENUS:
            if item["date"] == date_str:
                today_vendors.add(item["vendor"].strip().lower())
                
        # Find if any active vendor name is inside the poll name
        for v in today_vendors:
            if v in poll_name.lower():
                # Return original casing from the list
                for item in config.TODAYS_MENUS:
                    if item["vendor"].strip().lower() == v:
                        return item["vendor"].strip()
    except Exception as e:
        print("Error getting vendor for poll:", e)
        
    return default_vendor


def add_menu_in_memory(date_str: str, vendor: str, menu: str, price1: str, price2: str, posted_by: str, time_str: str) -> bool:
    """
    Adds today's parsed menu to the global config.TODAYS_MENUS list.
    Deduplicates based on Date, Vendor, and Menu.
    """
    # Check for duplicate
    for item in config.TODAYS_MENUS:
        if item["date"] == date_str and item["vendor"].lower() == vendor.lower() and item["menu"].lower() == menu.lower():
            print(f"Skipping duplicate menu entry (in-memory): {vendor} - {menu}")
            return False
            
    config.TODAYS_MENUS.append({
        "date": date_str,
        "vendor": vendor,
        "menu": menu,
        "price1": price1,
        "price2": price2,
        "posted_by": posted_by,
        "time": time_str
    })
    return True


def write_order_to_csv(date_str: str, time_str: str, person: str, vendor: str, selected_price: str):
    """
    Saves a poll vote to orders.csv. If a vote by the same Person on the same Date
    for the same Vendor already exists, it is overwritten (deduplicated).
    Otherwise, a new row is appended.
    """
    try:
        existing_rows = []
        if os.path.exists(config.ORDERS_CSV_FILE):
            with open(config.ORDERS_CSV_FILE, "r", newline="", encoding="utf-8") as f:
                reader = csv.reader(f)
                existing_rows = list(reader)
        
        headers = existing_rows[0] if existing_rows else ["Date", "Time", "Person", "Vendor", "Selected Price (₹)"]
        data_rows = existing_rows[1:] if existing_rows else []
        
        # Search for duplicate vote: matching Date (row[0]), Person (row[2]), and Vendor (row[3])
        duplicate_idx = -1
        for idx, row in enumerate(data_rows):
            if len(row) >= 4:
                if row[0] == date_str and row[2] == person and row[3] == vendor:
                    duplicate_idx = idx
                    break
        
        new_row = [date_str, time_str, person, vendor, selected_price]
        
        if duplicate_idx != -1:
            data_rows[duplicate_idx] = new_row
            print(f"Updated poll vote in orders.csv: {person} changed vote to '{selected_price}' for '{vendor}'")
        else:
            data_rows.append(new_row)
            print(f"Logged new poll vote in orders.csv: {person} voted '{selected_price}' for '{vendor}'")
            
        # Write everything back
        with open(config.ORDERS_CSV_FILE, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(headers)
            writer.writerows(data_rows)
            
    except Exception as e:
        print("Error writing order to CSV:", e)


def parse_message_regex(text: str):
    """
    Fallback regex parser to extract vendor name, menu items, and two prices.
    """
    m = config.PATTERN.search(text or "")
    if not m:
        return None
    return {
        "vendor": m.group("vendor").strip(),
        "menu": m.group("menu").strip(),
        "price1": m.group("p1"),
        "price2": m.group("p2"),
    }


def parse_message_llm(text: str) -> list:
    """
    Calls Groq API to parse the message text into structured meal information.
    Returns a list of dicts: [{"vendor": "...", "menu": "...", "price1": "...", "price2": "..."}]
    """
    if not config.GROQ_API_KEY:
        print("Warning: GROQ_API_KEY is not configured. Falling back to regex.")
        parsed = parse_message_regex(text)
        return [parsed] if parsed else []

    headers = {
        "Authorization": f"Bearer {config.GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    
    prompt = (
        "You are an assistant that extracts structured lunch meal orders/vendors from WhatsApp messages.\n"
        "Extract the following fields for each vendor/menu item listed in the message:\n"
        "- vendor: The name of the vendor (e.g. 'dineshbhai', 'Rameshbhai')\n"
        "- menu: The menu details (e.g. 'paneer + roti', 'aloo subji')\n"
        "- price1: The first price mentioned (usually standard/half price, e.g. 70). Output as integer or string representing number.\n"
        "- price2: The second price mentioned (usually full/premium price, e.g. 90). Output as integer or string representing number. If there is only one price, set price2 equal to price1.\n\n"
        "Return the output strictly as a JSON object with an 'items' key containing the list of objects. Do not include any markdown formatting, thoughts, explanations, or code blocks. If no items match, return `{\"items\": []}`.\n\n"
        "Example Input:\n"
        "dineshbhai (paneer + roti) - 70 rs - 90\nRameshbhai (aloo) - 60 rs\n\n"
        "Example Output:\n"
        "{\n"
        "  \"items\": [\n"
        "    {\"vendor\": \"dineshbhai\", \"menu\": \"paneer + roti\", \"price1\": \"70\", \"price2\": \"90\"},\n"
        "    {\"vendor\": \"Rameshbhai\", \"menu\": \"aloo\", \"price1\": \"60\", \"price2\": \"60\"}\n"
        "  ]\n"
        "}\n\n"
        f"Input message to extract:\n{text}"
    )

    data = {
        "model": config.GROQ_MODEL,
        "messages": [
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.0,
        "response_format": {"type": "json_object"}
    }

    try:
        response = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers=headers,
            json=data,
            timeout=15
        )
        if response.status_code != 200:
            print(f"Groq API Error {response.status_code}: {response.text}")
            parsed = parse_message_regex(text)
            return [parsed] if parsed else []

        res_json = response.json()
        content = res_json["choices"][0]["message"]["content"].strip()
        
        # Strip markdown code blocks if the model returned them
        if content.startswith("```"):
            start_idx = content.find("{")
            end_idx = content.rfind("}")
            if start_idx != -1 and end_idx != -1:
                content = content[start_idx:end_idx+1]

        parsed_data = json.loads(content)
        items = parsed_data.get("items", [])
        if isinstance(items, list):
            return items
        return []
    except Exception as e:
        print(f"Error parsing message with Groq: {e}")
        parsed = parse_message_regex(text)
        return [parsed] if parsed else []


def save_poll(msg_id: str, poll_name: str, options: list):
    """
    Saves metadata about a newly created poll into a local JSON database.
    """
    try:
        if os.path.exists(config.POLLS_DB_FILE):
            with open(config.POLLS_DB_FILE, "r") as f:
                db = json.load(f)
        else:
            db = {}
    except Exception:
        db = {}
    
    vendor = get_vendor_for_poll(poll_name)
    now_dt = datetime.datetime.now(config.TZ)
    date_str = now_dt.strftime("%Y-%m-%d")
    
    db[msg_id] = {
        "name": poll_name,
        "vendor": vendor,
        "options": options,
        "date": date_str
    }
    
    try:
        with open(config.POLLS_DB_FILE, "w") as f:
            json.dump(db, f, indent=2)
    except Exception as e:
        print("Error saving poll to DB:", e)


def get_poll(msg_id: str) -> dict:
    """
    Retrieves metadata about an existing poll from the JSON database by message ID.
    """
    try:
        if os.path.exists(config.POLLS_DB_FILE):
            with open(config.POLLS_DB_FILE, "r") as f:
                db = json.load(f)
                return db.get(msg_id)
    except Exception:
        pass
    return None


def extract_poll_creation(message) -> dict:
    """
    Helper function to inspect multiple poll creation versions in protobuf.
    """
    for field in [
        "pollCreationMessage",
        "pollCreationMessageV2",
        "pollCreationMessageV3",
        "pollCreationMessageV4",
        "pollCreationMessageV5",
        "pollCreationMessageV6",
    ]:
        try:
            if field in message.DESCRIPTOR.fields_by_name:
                if message.HasField(field):
                    poll = getattr(message, field)
                    options = [opt.optionName for opt in poll.options if opt.optionName]
                    return {"name": poll.name, "options": options}
        except Exception:
            pass
    return None


def generate_monthly_summary(month_prefix: str = None, start_date: str = None, end_date: str = None, update_csv: bool = True) -> str:
    """
    Reads the local orders.csv, aggregates the orders for the given month or date range,
    updates summary.csv, and returns a formatted text report.
    """
    try:
        # Determine filtering parameters
        if not start_date and not end_date and not month_prefix:
            now_dt = datetime.datetime.now(config.TZ)
            month_prefix = now_dt.strftime("%Y-%m")
            
        if start_date and end_date:
            period_desc = f"{start_date} to {end_date}"
        elif month_prefix:
            period_desc = month_prefix
        else:
            period_desc = "All Time"

        print(f"Regenerating Summary for {period_desc}...")
        order_rows = []
        if os.path.exists(config.ORDERS_CSV_FILE):
            with open(config.ORDERS_CSV_FILE, "r", newline="", encoding="utf-8") as f:
                reader = csv.reader(f)
                order_rows = list(reader)
                
        if len(order_rows) <= 1:
            return f"No orders found to summarize for the period: {period_desc}."

        summary = {}  # voter_name -> {"total_orders": 0, "total_amount": 0, "breakdown": {vendor: count}}
        
        for row in order_rows[1:]:
            if len(row) < 5:
                continue
            date_val = row[0].strip()
            voter = row[2].strip()
            vendor = row[3].strip()
            price_val = row[4].strip()
            
            # Apply filters
            if start_date and end_date:
                if not (start_date <= date_val <= end_date):
                    continue
            elif month_prefix:
                if not date_val.startswith(month_prefix):
                    continue
                
            prices = []
            for p in price_val.split(","):
                p_clean = p.strip()
                if p_clean.isdigit():
                    prices.append(int(p_clean))
            
            if not prices:
                continue
                
            if voter not in summary:
                summary[voter] = {"total_orders": 0, "total_amount": 0, "breakdown": {}}
                
            summary[voter]["total_orders"] += len(prices)
            summary[voter]["total_amount"] += sum(prices)
            summary[voter]["breakdown"][vendor] = summary[voter]["breakdown"].get(vendor, 0) + len(prices)

        if not summary:
            return f"No orders found to summarize for the period: {period_desc}."

        summary_rows = [["Person", "Total Orders", "Total Amount (₹)", "Breakdown"]]
        
        # Build text report
        report_lines = [
            f"📊 *DRC Lunch Hisab ({period_desc})*",
            "-----------------------------------"
        ]
        
        for voter, data in sorted(summary.items()):
            breakdown_parts = []
            for vendor, count in sorted(data["breakdown"].items()):
                breakdown_parts.append(f"{vendor}x{count}")
            breakdown_str = ", ".join(breakdown_parts)
            
            summary_rows.append([
                voter,
                str(data["total_orders"]),
                str(data["total_amount"]),
                breakdown_str
            ])
            
            report_lines.append(
                f"👤 *{voter}*: {data['total_orders']} orders | *₹{data['total_amount']}* ({breakdown_str})"
            )
            
        report_lines.append("-----------------------------------")
        if update_csv:
            report_lines.append("_Full details updated in summary.csv._")
            # Overwrite summary.csv file
            with open(config.SUMMARY_CSV_FILE, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerows(summary_rows)
            print("summary.csv successfully updated!")
        else:
            report_lines.append("_Custom query report (summary.csv not updated)._")
        
        return "\n".join(report_lines)
        
    except Exception as e:
        print("Error generating summary:", e)
        return "⚠️ Error occurred while generating the summary."


def extract_text(message) -> str:
    """Text can live in .conversation (plain) or extendedTextMessage (with quote/link)."""
    if getattr(message, "conversation", None):
        return message.conversation
    ext = getattr(message, "extendedTextMessage", None)
    if ext and getattr(ext, "text", None):
        return ext.text
    return ""


def get_voter_display(evt) -> str:
    """
    Helper function to build sender's display string: 'Pushname (Phone)' or 'Phone'.
    """
    voter_jid = Jid2String(evt.Info.MessageSource.Sender)
    voter_phone = voter_jid.split("@")[0]
    voter_name = evt.Info.Pushname
    return f"{voter_name} ({voter_phone})" if voter_name else voter_phone
