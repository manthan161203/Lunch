import csv
import datetime
import hashlib
import os
import re
from neonize.client import NewClient
from neonize.utils.jid import Jid2String

import config
import utils

def handle_hisab_command(client: NewClient, evt, text: str):
    """
    Parses and processes '!hisab' commands to return aggregate ordering costs.
    Supports:
      - !hisab (current month)
      - !hisab YYYY-MM (specific month)
      - !hisab YYYY-MM-DD (single day)
      - !hisab YYYY-MM-DD to YYYY-MM-DD (date range)
      - !hisab YYYY-MM-DD YYYY-MM-DD (date range)
    """
    text_clean = text.strip()
    date_pattern = r"\d{4}-\d{2}-\d{2}"
    month_pattern = r"\d{4}-\d{2}"
    
    # 1. Try parsing date range: "!hisab YYYY-MM-DD to YYYY-MM-DD" or "!hisab YYYY-MM-DD YYYY-MM-DD"
    range_match = re.search(rf"(!hisab\s+)({date_pattern})\s+(?:to\s+)?({date_pattern})", text_clean, re.IGNORECASE)
    if range_match:
        start_date = range_match.group(2)
        end_date = range_match.group(3)
        try:
            report = utils.generate_monthly_summary(start_date=start_date, end_date=end_date, update_csv=False)
            client.send_message(evt.Info.MessageSource.Chat, report)
        except Exception as e:
            print("Error replying to !hisab range:", e)
        return

    # 2. Try parsing single month or single day argument
    parts = text_clean.split()
    if len(parts) > 1:
        arg = parts[1].strip()
        # Single day query: YYYY-MM-DD
        if re.match(rf"^{date_pattern}$", arg):
            try:
                report = utils.generate_monthly_summary(start_date=arg, end_date=arg, update_csv=False)
                client.send_message(evt.Info.MessageSource.Chat, report)
            except Exception as e:
                print("Error replying to !hisab single day:", e)
            return
        # Month query: YYYY-MM
        elif re.match(rf"^{month_pattern}$", arg):
            try:
                report = utils.generate_monthly_summary(month_prefix=arg, update_csv=True)
                client.send_message(evt.Info.MessageSource.Chat, report)
            except Exception as e:
                print("Error replying to !hisab month:", e)
            return
        else:
            try:
                client.send_message(
                    evt.Info.MessageSource.Chat,
                    "⚠️ Invalid format. Use:\n"
                    "- `!hisab` (current month)\n"
                    "- `!hisab YYYY-MM` (e.g. `!hisab 2026-06`)\n"
                    "- `!hisab YYYY-MM-DD to YYYY-MM-DD` (e.g. `!hisab 2026-06-01 to 2026-06-15`)"
                )
            except Exception as e:
                print("Error sending validation warning:", e)
            return

    # 3. Default to current month
    try:
        report = utils.generate_monthly_summary(update_csv=True)
        client.send_message(evt.Info.MessageSource.Chat, report)
    except Exception as e:
        print("Error replying to default !hisab:", e)


def handle_hisab_done_command(client: NewClient, evt):
    """
    Settle all current orders:
    1. Generates a final summary of all current orders.
    2. Overwrites summary.csv with this final summary.
    3. Archives both orders.csv and summary.csv by renaming them with a timestamp.
    4. Creates empty orders.csv and summary.csv.
    5. Sends confirmation and final report to the group.
    """
    try:
        # Check if there are active orders to settle
        has_orders = False
        if os.path.exists(config.ORDERS_CSV_FILE):
            with open(config.ORDERS_CSV_FILE, "r", newline="", encoding="utf-8") as f:
                reader = csv.reader(f)
                rows = list(reader)
                if len(rows) > 1:
                    has_orders = True
        
        if not has_orders:
            client.send_message(evt.Info.MessageSource.Chat, "⚠️ No active orders found to settle.")
            return

        # Generate a final summary report of all orders (regardless of date range/month)
        report = utils.generate_monthly_summary(start_date="0000-00-00", end_date="9999-99-99", update_csv=True)
        
        # Format a timestamp for archiving
        now_dt = datetime.datetime.now(config.TZ)
        timestamp = now_dt.strftime("%Y%m%d_%H%M%S")
        
        # Archive files
        archived_orders_file = f"orders_archived_{timestamp}.csv"
        archived_summary_file = f"summary_archived_{timestamp}.csv"
        
        if os.path.exists(config.ORDERS_CSV_FILE):
            os.rename(config.ORDERS_CSV_FILE, archived_orders_file)
        if os.path.exists(config.SUMMARY_CSV_FILE):
            os.rename(config.SUMMARY_CSV_FILE, archived_summary_file)
            
        # Re-initialize fresh storage files
        utils.initialize_storage()
        
        # Build success response message
        confirmation_msg = (
            f"✅ *Lunch Hisab Settled!* \n\n"
            f"All active orders have been finalized and archived to:\n"
            f"- `{archived_orders_file}`\n"
            f"- `{archived_summary_file}`\n\n"
            f"A fresh orders sheet has been started.\n\n"
            f"📋 *Final Settled Summary:*\n"
            f"{report}"
        )
        
        client.send_message(evt.Info.MessageSource.Chat, confirmation_msg)
        print(f"Settled and archived current orders to {archived_orders_file}")
        
    except Exception as e:
        print("Error handling !hisab-done command:", e)
        try:
            client.send_message(evt.Info.MessageSource.Chat, "⚠️ An error occurred while settling the orders.")
        except Exception:
            pass


def handle_poll_creation(evt) -> bool:
    """
    Extracts and stores poll options if the message is a new poll.
    """
    poll_info = utils.extract_poll_creation(evt.Message)
    if poll_info:
        msg_id = evt.Info.ID
        utils.save_poll(msg_id, poll_info["name"], poll_info["options"])
        print(f"Saved poll creation: {poll_info['name']} (ID: {msg_id})")
        return True
    return False


def handle_poll_vote(client: NewClient, evt) -> bool:
    """
    Decrypts and records the vote update when a user interacts with a poll.
    """
    try:
        if "pollUpdateMessage" not in evt.Message.DESCRIPTOR.fields_by_name:
            return False
        if not evt.Message.HasField("pollUpdateMessage"):
            return False
    except Exception:
        return False

    try:
        poll_vote = client.decrypt_poll_vote(evt)
        creation_msg_id = evt.Message.pollUpdateMessage.pollCreationMessageKey.ID
        
        poll_info = utils.get_poll(creation_msg_id)
        voter_display = utils.get_voter_display(evt)
        
        now_dt = datetime.datetime.now(config.TZ)
        date_str = now_dt.strftime("%Y-%m-%d")
        time_str = now_dt.strftime("%H:%M")
        
        if poll_info:
            poll_name = poll_info["name"]
            vendor_name = poll_info.get("vendor", poll_name)
            options = poll_info["options"]
            
            voted_options = []
            for opt in options:
                opt_hash = hashlib.sha256(opt.encode("utf-8")).digest()
                if opt_hash in poll_vote.selectedOptions:
                    voted_options.append(opt)
            
            if not voted_options:
                # Vote cleared: remove the person's order for this vendor entirely.
                utils.delete_order_from_csv(date_str, voter_display, vendor_name)
                utils.generate_monthly_summary()
                return True

            voted_str = ", ".join(voted_options)

            # Write order locally
            utils.write_order_to_csv(date_str, time_str, voter_display, vendor_name, voted_str)

            # Automatically regenerate monthly summary
            utils.generate_monthly_summary()
        else:
            print(f"Skipped logging poll vote: Poll ID {creation_msg_id} is unknown (options not in database)")
    except Exception as e:
        print("Error handling poll vote:", e)
    return True


def handle_button_response(evt) -> bool:
    """
    Records a selection from a user clicking interactive message buttons.
    """
    try:
        if "buttonsResponseMessage" not in evt.Message.DESCRIPTOR.fields_by_name:
            return False
        if not evt.Message.HasField("buttonsResponseMessage"):
            return False
    except Exception:
        return False

    try:
        resp = evt.Message.buttonsResponseMessage
        selected_text = resp.selectedDisplayText
        voter_display = utils.get_voter_display(evt)
        
        now_dt = datetime.datetime.now(config.TZ)
        date_str = now_dt.strftime("%Y-%m-%d")
        time_str = now_dt.strftime("%H:%M")
        
        # Write button click to orders
        utils.write_order_to_csv(date_str, time_str, voter_display, "Button Response", selected_text)
        
        # Automatically regenerate monthly summary
        utils.generate_monthly_summary()
    except Exception as e:
        print("Error handling button response:", e)
    return True


def handle_list_response(evt) -> bool:
    """
    Records a selection from a user selecting an option from interactive list messages.
    """
    try:
        if "listResponseMessage" not in evt.Message.DESCRIPTOR.fields_by_name:
            return False
        if not evt.Message.HasField("listResponseMessage"):
            return False
    except Exception:
        return False

    try:
        resp = evt.Message.listResponseMessage
        selected_text = resp.title
        voter_display = utils.get_voter_display(evt)
        
        now_dt = datetime.datetime.now(config.TZ)
        date_str = now_dt.strftime("%Y-%m-%d")
        time_str = now_dt.strftime("%H:%M")
        
        # Write list response to orders
        utils.write_order_to_csv(date_str, time_str, voter_display, "List Response", selected_text)
        
        # Automatically regenerate monthly summary
        utils.generate_monthly_summary()
    except Exception as e:
        print("Error handling list response:", e)
    return True


def handle_menu_posting(evt, text: str) -> bool:
    """
    Extracts menu text using regex/LLM and records it in-memory.
    """
    parsed_items = utils.parse_message_llm(text)
    if not parsed_items:
        print(f"Menu NOT detected (parser returned no items) for text: {text!r}")
        return False

    now_dt = datetime.datetime.now(config.TZ)
    date_str = now_dt.strftime("%Y-%m-%d")
    time_str = now_dt.strftime("%H:%M")

    for item in parsed_items:
        vendor = (item.get("vendor") or "").strip()
        menu = (item.get("menu") or "").strip()
        price1 = str(item.get("price1") or "").strip()
        price2 = str(item.get("price2") or "").strip()

        if not price2 and price1:
            price2 = price1

        if not vendor or not menu or not price1:
            print(f"Skipping menu item (missing vendor/menu/price): {item!r} from text: {text!r}")
            continue

        posted_by = utils.get_voter_display(evt)

        # Log menu in-memory
        logged = utils.add_menu_in_memory(date_str, vendor, menu, price1, price2, posted_by, time_str)
        if logged:
            print("Logged Menu (in-memory):", item)
    return True


def handle_undecryptable_message(evt):
    """
    Fires when WhatsApp couldn't decrypt a message (usually a missing sender key).
    Stays quiet for other groups, but prints a LOUD alert if it happens in the
    lunch group, since that could be a menu/poll/vote we've silently lost.
    """
    try:
        chat_jid = Jid2String(evt.Info.MessageSource.Chat)
    except Exception:
        return

    if config.GROUP_JID not in chat_jid:
        # Some other group — harmless, ignore.
        return

    try:
        sender = utils.get_voter_display(evt)
    except Exception:
        sender = "unknown sender"

    try:
        ts = datetime.datetime.fromtimestamp(evt.Info.Timestamp, tz=config.TZ).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        ts = "unknown time"

    print("=" * 60)
    print("⚠️  DECRYPT FAILURE IN LUNCH GROUP — MESSAGE LOST")
    print(f"    From : {sender}")
    print(f"    Time : {ts}")
    print(f"    Msg ID: {getattr(evt.Info, 'ID', '?')}")
    print("    ACTION: Ask this person to RE-POST their menu/poll or re-vote.")
    print("=" * 60)


def process_incoming_message(evt):
    """
    Main orchestration logic for filtering, executing commands, and parsing inputs.
    """
    chat_jid = Jid2String(evt.Info.MessageSource.Chat)

    # Ignore stale messages from history sync on restart
    try:
        msg_ts = datetime.datetime.fromtimestamp(evt.Info.Timestamp, tz=config.TZ)
        if msg_ts < config.BOOT_TIME - datetime.timedelta(minutes=2):
            return
    except Exception:
        pass

    text = utils.extract_text(evt.Message)

    if config.DEBUG:
        print(f"[chat={chat_jid}] {text!r}")
        return

    if config.GROUP_JID not in chat_jid:
        return

    # 1. Handle command: !hisab-done
    if text.strip().lower().startswith("!hisab-done"):
        handle_hisab_done_command(config.client, evt)
        return

    # 2. Handle command: !hisab
    if text.strip().lower().startswith("!hisab"):
        handle_hisab_command(config.client, evt, text)
        return

    # 3. Check for Poll Creation
    if handle_poll_creation(evt):
        return

    # 4. Check for Poll Vote (Update)
    if handle_poll_vote(config.client, evt):
        return

    # 5. Check for Button Response
    if handle_button_response(evt):
        return

    # 6. Check for List Response
    if handle_list_response(evt):
        return

    # 7. Fallback: Parse Text Message / Menu Posting
    handle_menu_posting(evt, text)
