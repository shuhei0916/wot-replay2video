"""mod_shot_logger.wotmod パッケージをビルドして mods/2.3.0.2/ に配置する。"""

import zipfile
from pathlib import Path

MOD_PY = Path(__file__).parent / 'mod_shot_logger.py'
WOT_MODS_DIR = Path(r'C:\Games\World_of_Tanks_ASIA\mods\2.3.0.2')
OUTPUT = WOT_MODS_DIR / 'mod_shot_logger.wotmod'

META_XML = (
    '<?xml version="1.0" encoding="utf-8"?>\n'
    '<root>\n'
    '    <id>mod_shot_logger</id>\n'
    '    <version>1.0.0</version>\n'
    '    <name>Shot Logger</name>\n'
    '    <description>Logs player shot events to shot_events.json</description>\n'
    '    <author>wot-replay2video</author>\n'
    '</root>\n'
)

WOT_MODS_DIR.mkdir(parents=True, exist_ok=True)

with zipfile.ZipFile(OUTPUT, 'w', zipfile.ZIP_STORED) as zf:
    zf.writestr('meta.xml', META_XML)
    zf.write(MOD_PY, 'res/scripts/client/gui/mods/mod_shot_logger.py')

print(f'作成: {OUTPUT}')
print('内容:', zipfile.ZipFile(OUTPUT).namelist())
