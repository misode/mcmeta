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

@click.command()
@click.argument('version', required=True)
@click.option('--commit', is_flag=True, help='Whether to commit the exports')
@click.option('--init', is_flag=True, help='Whether to initialize the exports')
@click.option('--exports', '-e', multiple=True, required=True, type=click.Choice(['summary', 'data', 'assets', 'data-json', 'assets-json'], case_sensitive=True))
def main(version: str, commit: bool, init: bool, exports: tuple[str]):
	dotenv.load_dotenv()

	# === fetch manifest ===
	manifest = requests.get('https://launchermeta.mojang.com/mc/game/version_manifest_v2.json').json()
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

	# process and commit each version in the range
	process_versions = expand_version_range(version, versions)
	n = len(process_versions)
	if not process_versions:
		click.echo('❗ No versions to process')
		return

	if init:
		init_exports(versions[process_versions[0]]['releaseTime'], exports)

	try:
		os.remove('versions.json')
	except OSError:
		pass

	click.echo(f'🚧 Processing versions: {", ".join(process_versions)}')
	t0 = time.time()
	for i, v in enumerate(process_versions):
		t1 = time.time()
		process(v, versions, exports)
		if commit:
			create_commit(v, versions[v]['releaseTime'], exports)
		t2 = time.time()
		if n == 1:
			click.echo(f'✅ Done {v} ({(t2 - t1):.3f}s)')
		else:
			remaining = t2 - t0 + int(t2 - t1) * (n - i - 1)
			click.echo(f'✅ Done {v} ({i+1}/{n}) {format_time(t2 - t1)} ({format_time(t2 - t0)}/{format_time(remaining)})')


def format_time(seconds: float | int):
	seconds = int(seconds)
	if seconds <= 60:
		return f'{seconds}s'
	return f'{int(seconds/60)}m{seconds % 60}s'


def expand_version_range(version: str, versions: dict[str]):
	version_ids = list(versions.keys())
	if '..' in version:
		start, end = version.split('..')
		start_i = version_ids.index(start)
		end_i = version_ids.index(end)
		if end_i > start_i:
			return []
		return [version_ids[i] for i in range(start_i, end_i - 1, -1) if version_ids[i] != '20w14infinite']
	else:
		return [version]


def get_version_meta(version: str, versions: dict[str], jar: str = None):
	os.makedirs('tmp', exist_ok=True)

	if not jar:
		launchermeta_url = versions[version]['url']
		launchermeta = requests.get(launchermeta_url).json()

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
	launchermeta = requests.get(launchermeta_url).json()

	for side in ['server', 'client']:
		side_url = launchermeta['downloads'][side]['url']
		side_content = requests.get(side_url).content
		with open(f'{side}.jar', 'wb') as f:
			f.write(side_content)

	# === extract client jar ===
	shutil.rmtree('data/assets', ignore_errors=True)
	shutil.rmtree('data/data', ignore_errors=True)
	with zipfile.ZipFile('client.jar', 'r') as jar:
		for file in jar.namelist():
			if file.endswith('.mcassetsroot'):
				continue
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

	version_metas.append(get_version_meta(version, versions, 'client.jar'))
	has_version_ids = [v['id'] for v in version_metas]
	for v in expand_version_range(f'1.14..{version}', versions):
		if v not in has_version_ids:
			version_metas.append(get_version_meta(v, versions))
	version_metas.sort(key=lambda v: versions[v['id']]['index'])
	version_meta = version_metas[0]

	with open('versions.json', 'w') as f:
		json.dump(version_metas, f)

	# === run data generators ===
	shutil.rmtree('generated', ignore_errors=True)
	if versions[version]['index'] <= versions['21w39a']['index']:
		subprocess.run(['java', '-DbundlerMainClass=net.minecraft.data.Main', '-jar', 'server.jar', '--server', '--reports'], capture_output=True)
	else:
		subprocess.run(['java', '-cp', 'server.jar', 'net.minecraft.data.Main', '--server', '--reports'], capture_output=True)

	# === get vanilla worldgen === 
	if versions[version]['index'] <= versions['1.18-pre1']['index']:
		shutil.copytree('generated/reports/worldgen', 'data/data', dirs_exist_ok=True)
	elif versions[version]['index'] <= versions['20w28a']['index']:
		username = os.getenv('GITHUB_USERNAME')
		token = os.getenv('GITHUB_TOKEN')
		auth = requests.auth.HTTPBasicAuth(username, token) if username and token else None
		headers = { 'Accept': 'application/vnd.github.v3+json' }
		time = datetime.datetime.fromisoformat(versions[version]['releaseTime'])
		time += datetime.timedelta(days=1)
		res = requests.get(f'https://api.github.com/repos/slicedlime/examples/commits?until={time.isoformat()}', headers=headers, auth=auth)
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
				break

	# === collect summary of registries ===
	if 'summary' in exports:
		registries = dict()
		with open('generated/reports/registries.json', 'r') as f:
			for key, data in json.load(f).items():
				if key == 'minecraft:biome':
					key = 'worldgen/biome'
				entries = [e.removeprefix('minecraft:') for e in data['entries'].keys()]
				registries[key.removeprefix('minecraft:')] = sorted(entries)

		def listfiles(path: str, ext: str = 'json'):
			files = glob.glob(f'{path}/**/*.{ext}', recursive=True)
			entries = [e.replace('\\', '/', -1).removeprefix(f'{path}/').removesuffix(f'.{ext}') for e in files]
			return sorted(entries)

		for path, key in [('advancements', 'advancement'), ('loot_tables', 'loot_table'),('recipes', 'recipe'), ('tags/blocks', 'tag/block'), ('tags/entity_types', 'tag/entity_type'), ('tags/fluids', 'tag/fluid'), ('tags/game_events', 'tag/game_event'), ('tags/items', 'tag/item')]:
			registries[key] = listfiles(f'data/data/minecraft/{path}')

		for key in ['dimension', 'dimension_type', 'worldgen/biome', 'worldgen/configured_carver', 'worldgen/configured_feature', 'worldgen/configured_structure_feature', 'worldgen/configured_surface_builder', 'worldgen/noise', 'worldgen/noise_settings', 'worldgen/placed_feature', 'worldgen/processor_list', 'worldgen/template_pool']:
			if key not in registries:
				registries[key] = listfiles(f'data/data/minecraft/{key}')

		registries['structure'] = listfiles('data/data/minecraft/structures', 'nbt')

		for key in ['item_modifier', 'predicate', 'function']:
			registries[key] = []

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

	# === create sounds report ===
	def get_resource(hash: str):
		url = f'https://resources.download.minecraft.net/{hash[0:2]}/{hash}'
		return requests.get(url)

	if 'summary' in exports:
		assets_url = launchermeta['assetIndex']['url']
		assets = requests.get(assets_url).json()
		assets_hash = assets['objects']['minecraft/sounds.json']['hash']
		sounds: dict = get_resource(assets_hash).json()
		sounds = dict(sorted(sounds.items()))
		os.makedirs('sounds', exist_ok=True)

		cached = False
		try:
			with open(f'sounds/hash.txt', 'r') as f:
				cached = assets_hash == f.read()
		except:
			pass
		if not cached:
			with open(f'sounds/sounds.json', 'w') as f:
				json.dump(sounds, f)

			if 'assets' in exports:
				sound_paths = set(
					sound if type(sound) == str else sound['name']
					for event in sounds.values()
					for sound in event['sounds']
					if type(sound) == str or sound.get('type') != 'event'
				)
				print(f'Collecting {len(sound_paths)} sounds')
				shutil.rmtree('sounds/minecraft', ignore_errors=True)
				for path in sound_paths:
					full_path = f'minecraft/sounds/{path}.ogg'
					sound = get_resource(assets['objects'][full_path]['hash'])
					os.makedirs(os.path.normpath(os.path.join(f'sounds/{full_path}', '..')), exist_ok=True)
					with open(f'sounds/{full_path}', 'wb') as f:
						f.write(sound.content)

			with open(f'sounds/hash.txt', 'w') as f:
				f.write(assets_hash)

		for export in set(['assets', 'assets-json']).intersection(exports):
			shutil.copyfile('sounds/sounds.json', f'{export}/assets/minecraft/sounds.json')
		if 'assets' in exports:
			shutil.rmtree('assets/assets/minecraft/sounds', ignore_errors=True)
			shutil.copytree('sounds/minecraft/sounds', 'assets/assets/minecraft/sounds')

	# === read commands report ===
	if 'summary' in exports:
		with open('generated/reports/commands.json', 'r') as f:
			commands = json.load(f)

	# === export summary ===
	def create_summary(data, path):
		shutil.rmtree(path, ignore_errors=True)
		os.makedirs(path, exist_ok=True)
		with open(f'{path}/data.json', 'w') as f:
			json.dump(data, f, indent=2)
			f.write('\n')
		with open(f'{path}/data.min.json', 'w') as f:
			json.dump(data, f, separators=(',', ':'))
			f.write('\n')
		with open(f'{path}/data.msgpack', 'wb') as f:
			f.write(msgpack.packb(data))
		with open(f'{path}/data.json.gz', 'wb') as f:
			f.write(gzip.compress(json.dumps(data).encode('utf-8')))
		with open(f'{path}/data.msgpack.gz', 'wb') as f:
			f.write(gzip.compress(msgpack.packb(data)))

	if 'summary' in exports:
		create_summary(dict(sorted(registries.items())), 'summary/registries')
		create_summary(dict(sorted(blocks.items())), 'summary/blocks')
		create_summary(sounds, 'summary/sounds')
		create_summary(commands, 'summary/commands')
		create_summary(version_metas, 'summary/versions')

	# === export version.json to all ===
	for export in exports:
		with open(f'{export}/version.json', 'w') as f:
			json.dump(version_meta, f, indent=2)
			f.write('\n')

def init_exports(date: str, exports: tuple[str]):
	for export in exports:
		shutil.rmtree(export, ignore_errors=True)
		os.makedirs(export, exist_ok=True)
		os.chdir(export)
		subprocess.run(['git', 'init'])
		subprocess.run(['git', 'config', 'user.name', 'actions-user'])
		subprocess.run(['git', 'config', 'user.email', 'actions@github.com'])
		shutil.copyfile('../.gitattributes', f'.gitattributes')
		subprocess.run(['git', 'add', '.'])
		os.environ['GIT_AUTHOR_DATE'] = date
		os.environ['GIT_COMMITTER_DATE'] = date
		subprocess.run(['git', 'commit', '-q', '-m', f'🎉 Initial commit'])
		os.chdir('..')
		click.echo(f'🎉 Initialized {export} branch')


def create_commit(version: str, date: str, exports: tuple[str]):
	for export in exports:
		os.chdir(export)
		subprocess.run(['git', 'add', '.'])
		os.environ['GIT_AUTHOR_DATE'] = date
		os.environ['GIT_COMMITTER_DATE'] = date
		subprocess.run(['git', 'commit', '-q', '-m', f'🚀 Update {export} for {version}'])
		subprocess.run(['git', 'tag', f'{version}-{export}'])
		os.chdir('..')
		click.echo(f'🚀 Created commit on {export} branch')


if __name__ == '__main__':
	main()
