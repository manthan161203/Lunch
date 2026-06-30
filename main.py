"""
Lunch @DRC tiffin logger (Entrypoint)
------------------------------------
Listens to a WhatsApp group via neonize and appends each vendor's
menu + prices to local CSV files in real time.
"""

from neonize.events import ConnectedEv, MessageEv
from neonize.events import event as keep_alive
from config import client
from utils import initialize_storage
import handlers

@client.event(ConnectedEv)
def on_connected(client, _):
    print("Connected to WhatsApp. Listening...")

@client.event(MessageEv)
def on_message(client, evt):
    handlers.process_incoming_message(evt)

if __name__ == "__main__":
    initialize_storage()
    print("Connecting to WhatsApp...")
    client.connect()
    keep_alive.wait()
