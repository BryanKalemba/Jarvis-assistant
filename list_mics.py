"""
list_mics.py — run this once to find your microphone's device index.

Usage: python list_mics.py

If your PC has multiple microphones (built-in laptop mic, USB headset,
webcam mic, etc.), Jarvis might be listening to the wrong one by default.
This prints each one with its index number — put the right index in your
.env file as MIC_DEVICE_INDEX to force Jarvis to use it.
"""

import speech_recognition as sr

print("Available microphones:\n")
for index, name in enumerate(sr.Microphone.list_microphone_names()):
    print(f"  [{index}] {name}")

print(
    "\nFound the right one? Add this line to your .env file:\n"
    "  MIC_DEVICE_INDEX=<the number in brackets>\n"
    "Leave it blank/unset to keep using your system's default microphone."
)
