path = '/home/kirill/flux/ComfyUI/custom_nodes/ComfyUI-GGUF/loader.py'
s = open(path).read()

old = '    logging.info(f"Created tekken tokenizer with vocab size of {len(data[\'vocab\'])} (+{len(data[\'special_tokens\'])})")\n    del reader'
new = '    data["config"]["default_num_special_tokens"] = len(data["special_tokens"])\n    logging.info(f"Created tekken tokenizer with vocab size of {len(data[\'vocab\'])} (+{len(data[\'special_tokens\'])})")\n    del reader'

assert old in s, 'pattern not found'
s = s.replace(old, new, 1)
open(path, 'w').write(s)
print('patched OK')
