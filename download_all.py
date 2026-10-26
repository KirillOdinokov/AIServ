import time
from downloader import downloader as dl

models = ['flux2-klein-4b', 'flux2-dev-turbo', 'krea2-turbo', 'ideogram4']
for m in models:
    print('=== starting', m, '===')
    dl.download(m)

while True:
    active = False
    for m in models:
        st = dl.get_status(m)
        if st['status'] == 'downloading':
            active = True
            print('[' + m + '] ' + str(st['progress']) + '% ' + str(st['message']), flush=True)
    if not active:
        break
    time.sleep(30)

print('=== ALL DONE ===')
for m in models:
    st = dl.get_status(m)
    print(m, st['status'], st['message'])
