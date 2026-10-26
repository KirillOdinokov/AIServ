import os, sys
sys.path.insert(0, '/home/kirill/flux')
os.environ['HF_TOKEN'] = open('/home/kirill/flux/hf_token.txt').read().strip()
from downloader import downloader as dl
import time

models = ['wan2.1-t2v-14b', 'ltx-video-13b']
for m in models:
    if dl.is_downloaded(m):
        print(m, 'уже скачана')
        continue
    print('скачиваю', m)
    dl.download(m)

while True:
    active = False
    for m in models:
        st = dl.get_status(m)
        if st['status'] == 'downloading':
            active = True
            print(m, st['progress'], st['message'], flush=True)
    if not active:
        break
    time.sleep(30)
print('DONE')
