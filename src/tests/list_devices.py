from pprint import pprint
import pyaudio

audio = pyaudio.PyAudio()

for i in range(audio.get_device_count()):
    dev = audio.get_device_info_by_index(i)
    if 'name' in dev and 'Seiren' in dev['name']:
        pprint(dev)
    #pprint(dev)

