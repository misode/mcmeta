# MIT License
# 
# Copyright (c) 2022 Misode
# 
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
# 
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
# 
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

import click
import requests
import requests.auth
import zipfile
import subprocess
import json
import os
import os.path
import glob
import msgpack
import gzip
import shutil
import dotenv
import datetime
import re
import time
import image_packer.packer

EXPORTS = ('assets', 'assets-json', 'data', 'data-json', 'summary', 'registries', 'atlas')

APRIL_FOOLS = ('15w14a', '3D Shareware v1.34', '20w14infinite', '22w13oneblockatatime')

@click.command()
@click.option('--version', '-v')
@click.option('--file', '-f', type=click.File(), help='Custom version JSON file')
@click.option('--reset', is_flag=True, help='Whether to reset the exports')
@click.option('--fetch', is_flag=True, help='Whether to fetch from the remote at the start')
@click.option('--undo', help='The version to reset to')
@click.option('--commit', is_flag=True, help='Whether to commit the exports')
@click.option('--export', '-e', multiple=True, default=tuple(), type=click.Choice([*EXPORTS, 'all'], case_sensitive=True))
@click.option('--fixtags', is_flag=True, help='Whether to fix all the tags')
@click.option('--push', is_flag=True, help='Whether to push to the remote after each commit')
@click.option('--force', is_flag=True, help='Whether to force push')
@click.option('--branch', help='The export branch prefix to use')
def main(version: str | None, file, reset: bool, fetch: bool, undo: str | None, commit: bool, export: tuple[str], fixtags: bool, push: bool, force: bool, branch: str | None):
	dotenv.load_dotenv()
	if 'all' in export:
		export = EXPORTS

	# === fetch manifest ===
	manifest = requests.get('https://piston-meta.mojang.com/mc/game/version_manifest_v2.json').json()
	for v in manifest['versions']:
		v['id'] = v['id'].replace(' Pre-Release ', '-pre')
	version_ids = [v['id'] for v in manifest['versions']]

	# Fix version order anomaly around 1.16.5
	v1165 = version_ids.index('1.16.5')
	v20w51a = version_ids.index('20w51a')
	v1164 = version_ids.index('1.16.4')
	version_ids = [*version_ids[:v1165], *version_ids[v20w51a:v1164], *version_ids[v1165:v20w51a], *version_ids[v1164:]]

	unordered_versions = { v['id']: dict(**v, index=version_ids.index(v['id'])) for v in manifest['versions'] }
	versions = { v: unordered_versions[v] for v in version_ids }

	if file:
		assert version
		launchermeta = json.load(file)
		versions[version] = {
			'id': version,
			'type': launchermeta['type'],
			'url': launchermeta,
			'releaseTime': launchermeta['releaseTime'],
			'sha1': 'unknown',
			'index': -1,
		}

	# process and commit each version in the range
	process_versions = expand_version_range(version, versions)
	n = len(process_versions)
	start_date = versions[process_versions[0]]['releaseTime'] if process_versions else None
	init_exports(start_date, reset, fetch, undo, export, branch)

	# try:
	# 	os.remove('versions.json')
	# except OSError:
	# 	pass

	if process_versions:
		click.echo(f'???? Processing versions: {", ".join(process_versions)}')
		t0 = time.time()
		for i, v in enumerate(process_versions):
			t1 = time.time()
			process(v, versions, export)
			if commit:
				create_commit(v, versions[v]['releaseTime'], push, force, export, branch)
			t2 = time.time()
			if n == 1:
				click.echo(f'??? Done {v} ({format_time(t2 - t1)})')
			else:
				remaining = t2 - t0 + int(t2 - t1) * (n - i - 1)
				click.echo(f'??? Done {v} ({i+1} / {n}) {format_time(t2 - t1)} ({format_time(t2 - t0)} / {format_time(remaining)})')

	if fixtags:
		fix_tags(export, branch)

	if (not version or fixtags) and push:
		create_commit(None, None, push, force, export, branch)


def format_time(seconds: float | int):
	seconds = int(seconds)
	if seconds <= 60:
		return f'{seconds}s'
	minutes = int(seconds/60)
	if minutes <= 60:
		return f'{minutes}m {seconds % 60}s'
	return f'{int(minutes/60)}h {minutes%60}m {seconds%60}s'


def expand_version_range(version: str | None, versions: dict[str]):
	if version is None:
		return []
	version_ids = list(versions.keys())
	if '..' in version:
		start, end = version.split('..')
		start_i = version_ids.index(start)
		end_i = version_ids.index(end)
		if end_i > start_i:
			click.echo('??? No versions in range')
			return []
		return [version_ids[i] for i in range(start_i, end_i - 1, -1) if version_ids[i] not in APRIL_FOOLS]
	else:
		return [version]


def get_version_meta(version: str, versions: dict[str], jar: str = None):
	os.makedirs('tmp', exist_ok=True)

	if not jar:
		launchermeta_url = versions[version]['url']
		launchermeta = requests.get(launchermeta_url).json() if type(launchermeta_url) is str else launchermeta_url

		client_url = launchermeta['downloads']['client']['url']
		client = requests.get(client_url).content
		jar = 'tmp/client.jar'
		with open(jar, 'wb') as f:
			f.write(client)

	with zipfile.ZipFile(jar, 'r') as f:
		f.extract('version.json', 'tmp')

	with open('tmp/version.json', 'r') as f:
		data = json.load(f)

	pack = data['pack_version']

	return {
		'id': version,
		'name': data['name'],
		'release_target': data['release_target'],
		'type': versions[version]['type'],
		'stable': data['stable'],
		'data_version': data['world_version'],
		'protocol_version': data['protocol_version'],
		'data_pack_version': pack if type(pack) == int else pack['data'],
		'resource_pack_version': pack if type(pack) == int else pack['resource'],
		'build_time': data['build_time'],
		'release_time': versions[version]['releaseTime'],
		'sha1': versions[version]['sha1']
	}


def process(version: str, versions: dict[str], exports: tuple[str]):
	version_ids = list(versions.keys())

	# === fetch version jars ===
	launchermeta_url = versions[version]['url']
	launchermeta = requests.get(launchermeta_url).json() if type(launchermeta_url) is str else launchermeta_url

	for side in ['server', 'client']:
		side_url = launchermeta['downloads'][side]['url']
		side_content = requests.get(side_url).content
		with open(f'{side}.jar', 'wb') as f:
			f.write(side_content)

	# === extract client jar ===
	shutil.rmtree('assets/assets', ignore_errors=True)
	shutil.rmtree('assets-json/assets', ignore_errors=True)
	shutil.rmtree('data/data', ignore_errors=True)
	shutil.rmtree('data-json/data', ignore_errors=True)
	with zipfile.ZipFile('client.jar', 'r') as jar:
		for file in jar.namelist():
			if file.endswith('.mcassetsroot'):
				continue
			if file.endswith('pack.mcmeta'):
				jar.extract(file, 'data')
			for part in ['assets', 'data']:
				if file.startswith(f'{part}/'):
					jar.extract(file, part)
					if f'{part}-json' in exports and file.endswith('.json'):
						jar.extract(file, f'{part}-json')

	# === update version metas ===
	try:
		with open('versions.json', 'r') as f:
			version_metas = json.load(f)
	except:
		version_metas = []

	if version not in [v['id'] for v in version_metas]:
		version_metas.append(get_version_meta(version, versions, 'client.jar'))
	has_version_ids = [v['id'] for v in version_metas]
	for v in expand_version_range(f'1.14..{version}', versions):
		if v not in has_version_ids:
			version_metas.append(get_version_meta(v, versions))
	version_metas.sort(key=lambda v: versions[v['id']]['index'])
	version_meta = next(v for v in version_metas if v['id'] == version)

	with open('versions.json', 'w') as f:
		json.dump(version_metas, f)

	# === reconstruct data pack.mcmeta ===
	if versions[version]['index'] <= versions['20w45a']['index']:
		pack = {
			'pack': {
				'description': 'The default data for Minecraft',
				'pack_format': version_meta['data_pack_version']
			}
		}
		for e in ['data', 'data-json']:
				with open(f'{e}/pack.mcmeta', 'w') as f:
					json.dump(pack, f, indent=4)

	# === run data generators ===
	if 'data' in exports or 'data-json' in exports or 'summary' in exports or 'registries' in exports:
		click.echo('Running data generator')
		shutil.rmtree('generated', ignore_errors=True)
		if versions[version]['index'] <= versions['21w39a']['index']:
			subprocess.run(['java', '-DbundlerMainClass=net.minecraft.data.Main', '-jar', 'server.jar', '--reports'], capture_output=True)
		else:
			subprocess.run(['java', '-cp', 'server.jar', 'net.minecraft.data.Main', '--reports'], capture_output=True)

	# === get vanilla worldgen ===
	if 'data' in exports or 'data-json' in exports or 'summary' in exports or 'registries' in exports:
		if versions[version]['index'] <= versions['22w19a']['index']:
			shutil.copytree('generated/reports/minecraft', 'data/data/minecraft', dirs_exist_ok=True)
			shutil.copytree('generated/reports/minecraft', 'data-json/data/minecraft', dirs_exist_ok=True)
		elif versions[version]['index'] <= versions['1.18-pre1']['index']:
			shutil.copytree('generated/reports/worldgen', 'data/data', dirs_exist_ok=True)
			shutil.copytree('generated/reports/worldgen', 'data-json/data', dirs_exist_ok=True)
		elif versions[version]['index'] <= versions['20w28a']['index']:
			click.echo('Downloading vanilla worldgen')
			username = os.getenv('github-username')
			token = os.getenv('github-token')
			auth = requests.auth.HTTPBasicAuth(username, token) if username and token else None
			headers = { 'Accept': 'application/vnd.github.v3+json' }
			released = datetime.datetime.fromisoformat(versions[version]['releaseTime'])
			released += datetime.timedelta(days=1)
			res = requests.get(f'https://api.github.com/repos/slicedlime/examples/commits?until={released.isoformat()}', headers=headers, auth=auth)
			click.echo(f'Remaining GitHub requests: {res.headers["X-RateLimit-Remaining"]}/{res.headers["X-RateLimit-Limit"]}')
			commits = res.json()
			for id in version_ids[versions[version]['index']:]:
				sha = next((c['sha'] for c in commits if re.match(f'Update to {id}\\.?$', c['commit']['message'])), None)
				if sha is None and id == '20w28a':
					sha = 'd304a1dcf330005e617a78cef4e492ab3e2c09b0'
				if sha:
					content = requests.get(f'https://raw.githubusercontent.com/slicedlime/examples/{sha}/vanilla_worldgen.zip').content
					with open('vanilla_worldgen.zip', 'wb') as f:
						f.write(content)
					zip = zipfile.ZipFile('vanilla_worldgen.zip', 'r')
					zip.extractall('data/data/minecraft')
					zip.extractall('data-json/data/minecraft')
					break

	# === reconstruct dimensions ===
	if versions[version]['index'] <= versions['22w11a']['index'] and not os.path.isdir('data/data/minecraft/dimension'):
		with open('data/data/minecraft/worldgen/world_preset/normal.json', 'r') as f:
			world_preset = json.load(f)
		for key, dimension in world_preset['dimensions'].items():
			preset = dimension['generator'].get('biome_source', dict()).get('preset', '')
			try:
				with open(f'generated/reports/biome_parameters/{preset.replace(":", "/")}.json', 'r') as f:
					parameters = json.load(f)
					if parameters:
						parameters['type'] = 'minecraft:multi_noise'
						dimension['generator']['biome_source'] = parameters
			except:
				pass
			for e in ['data', 'data-json']:
				os.makedirs(f'{e}/data/minecraft/dimension/', exist_ok=True)
				with open(f'{e}/data/minecraft/dimension/{key.removeprefix("minecraft:")}.json', 'w') as f:
					json.dump(dimension, f, indent=2)

	# === stabilize ordering in some data files ===
	reorders = [
		('advancements/adventure/adventuring_time',
			[('criteria', None), ('requirements', lambda e: e[0])]),
		('advancements/husbandry/complete_catalogue',
			[('criteria', None), ('requirements', lambda e: e[0])]),
		('advancements/nether/all_effects',
			[('criteria.all_effects.conditions.effects', None)]),
		('advancements/nether/all_potions',
			[('criteria.all_effects.conditions.effects', None)]),
		('loot_tables/chests/shipwreck_supply',
			[('pools.0.entries.[name=minecraft:suspicious_stew].functions.0.effects', lambda e: e['type'])]),
		('loot_tables/chests/ancient_city_ice_box',
			[('pools.0.entries.[name=minecraft:suspicious_stew].functions.0.effects', lambda e: e['type'])]),
		('loot_tables/gameplay/hero_of_the_village/fletcher_gift',
			[('pools.0.entries', lambda e: (e.get('functions')[-1].get('tag') or e.get('functions')[-1].get('id')) if e.get('functions') else e.get('name'))]),
		('worldgen/noise_settings/*', [('structures.structures', None)]),
		('worldgen/noise_settings/*', [('structures', None)]),
		('worldgen/configured_structure_feature/*', [('spawn_overrides', None)]),
		('worldgen/structure/*', [('spawn_overrides', None)]),
		('worldgen/flat_level_generator_preset/*', [('settings.structure_overrides', None)]),
		('worldgen/world_preset/*', [('dimensions', None)]),
	]

	for filepath, sorts in reorders:
		for file in glob.glob(f'data/data/minecraft/{filepath}.json'):
			with open(file, 'r') as f:
				root = json.load(f)

			for path, order in sorts:
				*parts, last = [int(p) if re.match('\d+', p) else p for p in path.split('.')]
				node = root
				for p in parts:
					if node is None:
						break
					if type(p) == str and p.startswith('['):
						key, value = p[1:-1].split('=')
						node = next((e for e in node if key in e and e[key] == value), None)
					elif type(node) == list:
						node = node[p]
					elif hasattr(node, 'get'):
						node = node.get(p, None)
					else:
						node = None
				if node is None or last not in node:
					break
				if type(node[last]) == dict:
					node[last] = dict(sorted(node[last].items(), key=order))
				elif type(node[last]) == list:
					node[last] = sorted(node[last], key=order)

			for export in set(['data', 'data-json']).intersection(exports):
				with open(f'{export}{file.removeprefix("data")}', 'w') as f:
					json.dump(root, f, indent=2)

	# === collect summary of registries ===
	if 'summary' in exports or 'registries' in exports:
		registries = dict()
		with open('generated/reports/registries.json', 'r') as f:
			for key, data in json.load(f).items():
				entries = [e.removeprefix('minecraft:') for e in data['entries'].keys()]
				registries[key.removeprefix('minecraft:')] = sorted(entries)

		def listfiles(path: str, ext: str = 'json'):
			files = glob.glob(f'{path}/**/*.{ext}', recursive=True)
			entries = [e.replace('\\', '/', -1).removeprefix(f'{path}/').removesuffix(f'.{ext}') for e in files]
			return sorted(entries)

		registry_overrides = {
			'advancements': 'advancement',
			'loot_tables': 'loot_table',
			'recipes': 'recipe',
			'tag/blocks': 'tag/block',
			'tag/entity_types': 'tag/entity_type',
			'tag/fluids': 'tag/fluid',
			'tag/game_events': 'tag/game_event',
			'tag/items': 'tag/item',
		}

		for pattern in ['', 'worldgen/', 'tags/', 'tags/worldgen/']:
			full_pattern = f'data/data/minecraft/{pattern}'
			types = [
				e.replace('\\', '/', -1).removeprefix(full_pattern).removesuffix('/')
				for e in glob.glob(f'{full_pattern}*/')
			]
			for typ in [t for t in types if t not in ['tags', 'worldgen', 'structures']]:
				registry_key = (pattern + typ).replace('tags/', 'tag/')
				registry_key = registry_overrides.get(registry_key, registry_key)
				registries[registry_key] = listfiles(full_pattern + typ)

		registries['structure'] = listfiles('data/data/minecraft/structures', 'nbt')

		for path, key in [('blockstates', 'block_definition'), ('font', 'font'), ('models', 'model')]:
			registries[key] = listfiles(f'assets/assets/minecraft/{path}')

		registries['texture'] = listfiles('assets/assets/minecraft/textures', 'png')

	# === simplify blocks report ===
	if 'summary' in exports:
		blocks = dict()
		with open('generated/reports/blocks.json', 'r') as f:
			for key, data in json.load(f).items():
				properties = data.get('properties')
				if properties:
					default = next(s.get('properties') for s in data['states'] if s.get('default'))
					blocks[key.removeprefix('minecraft:')] = (properties, default)

	# === download resources ===
	def get_resource(hash: str):
		url = f'https://resources.download.minecraft.net/{hash[0:2]}/{hash}'
		return requests.get(url)

	if 'summary' in exports:
		assets_hash = launchermeta['assetIndex']['sha1']
		assets_url = launchermeta['assetIndex']['url']
		assets = requests.get(assets_url).json()

		try:
			with open(f'resources/hash.txt', 'r') as f:
				cached = assets_hash == f.read()
		except:
			cached = False
		if not cached:
			if 'assets' in exports:
				click.echo(f'Downloading {len(assets["objects"])} resources')
				shutil.rmtree('resources', ignore_errors=True)
				os.makedirs('resources', exist_ok=True)
				for key, object in assets['objects'].items():
					sound = get_resource(object['hash'])
					os.makedirs(os.path.normpath(os.path.join(f'resources/{key}', '..')), exist_ok=True)
					with open(f'resources/{key}', 'wb') as f:
						f.write(sound.content)

			with open(f'resources/hash.txt', 'w') as f:
				f.write(assets_hash)

		for export, pattern in [('assets', '*.*'), ('assets-json', '*.json')]:
			if export in exports:
				for path in glob.glob(f'resources/**/{pattern}', recursive=True):
					if path.endswith('hash.txt'):
						continue
					target = f'{export}/assets{path.removeprefix("resources")}'
					if path.endswith('pack.mcmeta'):
						target = f'{export}/pack.mcmeta'
					os.makedirs(os.path.normpath(os.path.join(target, '..')), exist_ok=True)
					shutil.copyfile(path, target)
		if 'summary' in exports:
			with open(f'resources/minecraft/sounds.json', 'r') as f:
				sounds: dict = json.load(f)

	# === read commands report ===
	if 'summary' in exports:
		with open('generated/reports/commands.json', 'r') as f:
			commands = json.load(f)

	# === export summary ===
	def create_summary(data, path, clear=True, bin=True):
		if clear:
			shutil.rmtree(path, ignore_errors=True)
			os.makedirs(path, exist_ok=True)
		with open(f'{path}/data.json', 'w') as f:
			json.dump(data, f, indent=2)
			f.write('\n')
		with open(f'{path}/data.min.json', 'w') as f:
			json.dump(data, f, separators=(',', ':'))
			f.write('\n')
		if bin:
			with open(f'{path}/data.msgpack', 'wb') as f:
				f.write(msgpack.packb(data))
			with open(f'{path}/data.json.gz', 'wb') as f:
				f.write(gzip.compress(json.dumps(data).encode('utf-8')))
			with open(f'{path}/data.msgpack.gz', 'wb') as f:
				f.write(gzip.compress(msgpack.packb(data)))

	if 'summary' in exports:
		create_summary(dict(sorted(registries.items())), 'summary/registries')
		create_summary(dict(sorted(blocks.items())), 'summary/blocks')
		create_summary(dict(sorted(sounds.items())), 'summary/sounds')
		create_summary(commands, 'summary/commands')
		create_summary(version_metas, 'summary/versions')

	# === create texture atlas ===
	if 'atlas' in exports:
		click.echo('Packing textures into atlas')
		atlases = [
			('blocks', ['block'], 1024),
			('items', ['item'], 512),
			('entities', ['entity', 'entity/**'], 2048),
			('all', ['block', 'item', 'entity', 'entity/**'], 2048)
		]
		for name, folders, width in atlases:
			os.makedirs(f'atlas/{name}', exist_ok=True)
			prefix = 'assets/assets/minecraft/textures/'
			inputs = [f'{prefix}{f}/*.png' for f in folders]
			options = {
				'bg_color': (0, 0, 0, 0),
				'enable_auto_size': False,
			}
			image_packer.packer.pack(inputs, f'atlas/{name}/atlas.png', width, options)
			with open(f'atlas/{name}/atlas.json', 'r') as f:
				mapping = json.load(f)
				def key(filepath: str):
					return filepath.replace('\\', '/', -1).removeprefix(prefix).removesuffix('.png')
				mapping = {
					key(r['filepath']): [r['x'], r['y'], r['width'], r['height']]
					for r in mapping['regions'].values()
				}
			os.remove(f'atlas/{name}/atlas.json')
			create_summary(mapping, f'atlas/{name}', clear=False)

	# === create registries ===
	if 'registries' in exports:
		for key, entries in sorted(registries.items()):
			create_summary(entries, f'registries/{key}', bin=False)
		create_summary(sorted(registries.keys()), 'registries', clear=False, bin=False)

	# === export version.json to all ===
	for export in exports:
		with open(f'{export}/version.json', 'w') as f:
			json.dump(version_meta, f, indent=2)
			f.write('\n')

	# === copy pack.mcmeta to json exports ===
	for export in ['assets', 'data']:
		shutil.copyfile(f'{export}/pack.mcmeta', f'{export}-json/pack.mcmeta')


def init_exports(start_date: str | None, reset: bool, fetch: bool, undo: str | None, exports: tuple[str], branch: str | None):
	for export in exports:
		export_branch = f'{branch}-{export}' if branch else export
		if reset:
			shutil.rmtree(export, ignore_errors=True)
		os.makedirs(export, exist_ok=True)
		os.chdir(export)
		subprocess.run(['git', 'init', '-q'])
		subprocess.run(['git', 'checkout', '-q', '-b', export_branch], capture_output=True)
		subprocess.run(['git', 'config', 'user.name', 'actions-user'])
		subprocess.run(['git', 'config', 'user.email', 'actions@github.com'])
		if os.getenv('github-repository'):
			remotes = subprocess.run(['git', 'remote'], capture_output=True).stdout.decode('utf-8').split('\n')
			remote = f'https://x-access-token:{os.getenv("github-token")}@github.com/{os.getenv("github-repository")}'
			subprocess.run(['git', 'remote', 'set-url' if 'origin' in remotes else 'add', 'origin', remote])
		if fetch:
			subprocess.run(['git', 'fetch', '-q', '--tags', 'origin', export_branch])
			subprocess.run(['git', 'reset', '-q', '--hard', f'origin/{export_branch}'])
		elif reset:
			assert start_date, 'Cannot reset without a version'
			shutil.copyfile('../.gitattributes', f'.gitattributes')
			subprocess.run(['git', 'add', '.'])
			os.environ['GIT_AUTHOR_DATE'] = start_date
			os.environ['GIT_COMMITTER_DATE'] = start_date
			subprocess.run(['git', 'commit', '-q', '-m', f'???? Initial commit'])
		if undo:
			subprocess.run(['git', 'reset', '--hard', f'{undo}-{export}'])
		os.chdir('..')
		click.echo(f'???? Initialized {export} branch')


def create_commit(version: str | None, date: str | None, push: bool, force: bool, exports: tuple[str], branch: str | None):
	for export in exports:
		export_branch = f'{branch}-{export}' if branch else export
		os.chdir(export)
		if version:
			assert date
			subprocess.run(['git', 'add', '.'])
			os.environ['GIT_AUTHOR_DATE'] = date
			os.environ['GIT_COMMITTER_DATE'] = date
			subprocess.run(['git', 'commit', '-q', '-m', f'???? Update {export} for {version}'])
			subprocess.run(['git', 'tag', '-f', f'{version}-{export}'])
		if push:
			if force:
				subprocess.run(['git', 'push', '-f', '-q', '--tags', 'origin', export_branch])
			else:
				subprocess.run(['git', 'push', '-q', '--tags', 'origin', export_branch])
		os.chdir('..')
		click.echo(f'???? Created commit on {export_branch} branch')


def fix_tags(exports: tuple[str], branch: str | None):
	for export in exports:
		export_branch = f'{branch}-{export}' if branch else export
		os.chdir(export)
		taglist = subprocess.run(['git', 'tag', '-l'], capture_output=True).stdout.decode('utf-8').split('\n')
		subprocess.run(['git', 'tag', '-d', *taglist], capture_output=True)
		click.echo(f'???? Deleted {len(taglist) - 1} tags in {export_branch} branch')
		commits = [c
			for c in subprocess.run(['git', 'log', '--format=%h %f'], capture_output=True).stdout.decode('utf-8').split('\n')
			if re.match('^.* .*$', c) and not c.endswith('Initial-commit')
		]
		for c in commits:
			ref, message = c.split(' ')
			version = re.match(f'^Update-{export}-for-(.*)$', message.strip())[1]
			subprocess.run(['git', 'tag', f'{version}-{export}', ref.strip()],  capture_output=True)
		os.chdir('..')
		click.echo(f'??? Created {len(commits)} tags in {export_branch} branch')


if __name__ == '__main__':
	main()
