import click
import requests
import requests.auth
import zipfile
import subprocess
import json
import os
import glob
import msgpack
import gzip
import shutil
import dotenv
import datetime
import re

@click.command()
@click.argument('version')
@click.option('--all', is_flag=True, help='Whether to iterate over all version since')
@click.option('--commit', is_flag=True, help='Whether to commit the summary changes')
def main(version: str, all: bool, commit: bool):
	dotenv.load_dotenv()

	# === fetch manifest and version jars ===
	manifest = requests.get('https://launchermeta.mojang.com/mc/game/version_manifest.json').json()
	for v in manifest['versions']:
		v['id'] = v['id'].replace(' Pre-Release ', '-pre')
	all_versions = [v['id'] for v in manifest['versions']]

	# Fix version order anomaly around 1.16.5
	v1165 = all_versions.index('1.16.5')
	v20w51a = all_versions.index('20w51a')
	v1164 = all_versions.index('1.16.4')
	all_versions = [*all_versions[:v1165], *all_versions[v20w51a:v1164], *all_versions[v1165:v20w51a], *all_versions[v1164:]]

	versions = { v['id']: dict(**v, index=all_versions.index(v['id'])) for v in manifest['versions'] }

	for v in reversed(all_versions[:all_versions.index(version)+1]) if all else [version]:
		process(v, versions, all_versions)
		if commit:
			create_commit(v)
		click.echo(f'{v} ✅')


def process(version: str, versions: dict, all_versions: list):
	if version not in versions:
		click.echo(f'Unknown version "{version}"')
		return
	launchermeta_url = versions[version]['url']
	launchermeta = requests.get(launchermeta_url).json()

	for side in ['server', 'client']:
		side_url = launchermeta['downloads'][side]['url']
		side_content = requests.get(side_url).content
		with open(f'{side}.jar', 'wb') as f:
			f.write(side_content)

	# === extract version, assets and structures ===
	shutil.rmtree('assets', ignore_errors=True)
	shutil.rmtree('data', ignore_errors=True)
	with zipfile.ZipFile('client.jar', 'r') as jar:
		jar.extract('version.json')
		for file in jar.namelist():
			if file.startswith('assets/') or file.startswith('data/'):
				jar.extract(file)

	with open('version.json', 'r') as f:
		version_meta = json.load(f)
		version_meta['id'] = version_meta['id'].replace(' Pre-Release ', '-pre')

	# === run data generators ===
	shutil.rmtree('generated', ignore_errors=True)
	if versions[version]['index'] <= versions['1.18-pre1']['index']:
		subprocess.run(['java', '-DbundlerMainClass=net.minecraft.data.Main', '-jar', 'server.jar', '--server', '--reports', '--report'])
	elif versions[version]['index'] <= versions['21w39a']['index']:
		subprocess.run(['java', '-DbundlerMainClass=net.minecraft.data.Main', '-jar', 'server.jar', '--server', '--reports'])
	else:
		subprocess.run(['java', '-cp', 'server.jar', 'net.minecraft.data.Main', '--server', '--reports'])

	# === get vanilla worldgen === 
	if versions[version]['index'] <= versions['1.18-pre1']['index']:
		shutil.copytree('generated/reports/worldgen', 'data', dirs_exist_ok=True)
	elif versions[version]['index'] <= versions['20w28a']['index']:
		username = os.getenv('GITHUB_USERNAME')
		token = os.getenv('GITHUB_TOKEN')
		auth = requests.auth.HTTPBasicAuth(username, token) if username and token else None
		headers = { 'Accept': 'application/vnd.github.v3+json' }
		time = datetime.datetime.fromisoformat(versions[version]['releaseTime'])
		time += datetime.timedelta(hours=1)
		res = requests.get(f'https://api.github.com/repos/slicedlime/examples/commits?until={time.isoformat()}', headers=headers, auth=auth)
		click.echo(f'Remaining GitHub requests: {res.headers["X-RateLimit-Remaining"]}/{res.headers["X-RateLimit-Limit"]}')
		commits = res.json()
		for id in all_versions[versions[version]['index']:]:
			sha = next((c['sha'] for c in commits if re.match(f'Update to {id}\\.?$', c['commit']['message'])), None)
			if sha is None and id == '20w28a':
				sha = 'd304a1dcf330005e617a78cef4e492ab3e2c09b0'
			if sha:
				click.echo(f'Found matching vanilla worldgen commit {sha} ({id})')
				content = requests.get(f'https://raw.githubusercontent.com/slicedlime/examples/{sha}/vanilla_worldgen.zip').content
				with open('vanilla_worldgen.zip', 'wb') as f:
					f.write(content)
				zip = zipfile.ZipFile('vanilla_worldgen.zip', 'r')
				zip.extractall('data/minecraft')
				break

	# === collect summary of registries ===
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
		registries[key] = listfiles(f'data/minecraft/{path}')

	for key in ['dimension', 'dimension_type', 'worldgen/biome', 'worldgen/configured_carver', 'worldgen/configured_feature', 'worldgen/configured_structure_feature', 'worldgen/configured_surface_builder', 'worldgen/noise', 'worldgen/noise_settings', 'worldgen/placed_feature', 'worldgen/processor_list', 'worldgen/template_pool']:
		if key not in registries:
			registries[key] = listfiles(f'data/minecraft/{key}')

	registries['structure'] = listfiles('data/minecraft/structures', 'nbt')

	for key in ['item_modifier', 'predicate', 'function']:
		registries[key] = []

	for path, key in [('blockstates', 'block_definition'), ('font', 'font'), ('models', 'model')]:
		registries[key] = listfiles(f'assets/minecraft/{path}')

	registries['texture'] = listfiles('assets/minecraft/textures', 'png')

	# === simplify blocks report ===
	blocks = dict()
	with open('generated/reports/blocks.json', 'r') as f:
		for key, data in json.load(f).items():
			properties = data.get('properties')
			if properties:
				default = next(s.get('properties') for s in data['states'] if s.get('default'))
				blocks[key] = (properties, default)

	# === export summary ===
	def export(data, path):
		shutil.rmtree(path, ignore_errors=True)
		os.makedirs(path, exist_ok=True)
		with open(f'{path}/data.json', 'w') as f:
			json.dump(data, f, indent=2)
			f.write('\n')
		with open(f'{path}/data.min.json', 'w') as f:
			json.dump(data, f)
			f.write('\n')
		with open(f'{path}/data.msgpack', 'wb') as f:
			f.write(msgpack.packb(data))
		with open(f'{path}/data.json.gz', 'wb') as f:
			f.write(gzip.compress(json.dumps(data).encode('utf-8')))
		with open(f'{path}/data.msgpack.gz', 'wb') as f:
			f.write(gzip.compress(msgpack.packb(data)))

	export(dict(sorted(registries.items())), 'registries')
	export(dict(sorted(blocks.items())), 'blocks')
	export(version_meta, 'version')


def create_commit(version: str):
	subprocess.run(['git', 'config', 'user.name', 'actions-user'])
	subprocess.run(['git', 'config', 'user.email', 'actions@github.com'])
	subprocess.run(['git', 'checkout', '--orphan', 'summary'])
	subprocess.run(['git', 'reset'])
	subprocess.run(['git', 'add', '-f', 'registries', 'blocks', 'version'])
	subprocess.run(['git', 'commit', '-m', version])
	subprocess.run(['git', 'tag', version])


if __name__ == '__main__':
	main()
