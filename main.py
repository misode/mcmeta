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
@click.argument('version', required=True)
@click.option('--commit', is_flag=True, help='Whether to commit the exports')
@click.option('--init', is_flag=True, help='Whether to initialize the exports')
@click.option('--exports', '-e', multiple=True, required=True, type=click.Choice(['summary', 'data'], case_sensitive=True))
def main(version: str, commit: bool, init: bool, exports: tuple[str]):
	dotenv.load_dotenv()

	# === fetch manifest ===
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

	if '..' in version:
		start, end = version.split('..')
		start_i = all_versions.index(start)
		end_i = all_versions.index(end)
		if end_i > start_i:
			click.echo('❗ Invalid version range')
			return
		process_versions = [
			all_versions[i]
			for i in range(start_i, end_i - 1, -1)
			if all_versions[i] != '20w14infinite'
		]
	else:
		process_versions = [version]

	if init:
		init_output(versions[process_versions[0]]['releaseTime'], exports)

	click.echo(f'🚧 Processing versions: {", ".join(process_versions)}')
	for i, v in enumerate(process_versions):
		process(v, versions, all_versions, exports)
		if commit:
			create_commit(v, versions[v]['releaseTime'], exports)
		click.echo(f'✅ Done {v} ({i+1}/{len(process_versions)})')


def process(version: str, versions: dict, all_versions: list, exports: tuple[str]):
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
		jar.extract('version.json')
		for file in jar.namelist():
			if file.endswith('.mcassetsroot'):
				continue
			if file.startswith('assets/') or file.startswith('data/'):
				jar.extract(file, 'data')

	with open('version.json', 'r') as f:
		version_meta = json.load(f)
		version_meta['id'] = version

	# === run data generators ===
	shutil.rmtree('generated', ignore_errors=True)
	summary_flags = ['--server', '--reports'] if 'summary' in exports else []
	if versions[version]['index'] <= versions['1.18-pre1']['index']:
		subprocess.run(['java', '-DbundlerMainClass=net.minecraft.data.Main', '-jar', 'server.jar', *summary_flags, '--report'])
	elif versions[version]['index'] <= versions['21w39a']['index'] and summary_flags:
		subprocess.run(['java', '-DbundlerMainClass=net.minecraft.data.Main', '-jar', 'server.jar', *summary_flags])
	elif summary_flags:
		subprocess.run(['java', '-cp', 'server.jar', 'net.minecraft.data.Main', *summary_flags])

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
			registries[key] = listfiles(f'data/assets/minecraft/{path}')

		registries['texture'] = listfiles('data/assets/minecraft/textures', 'png')

	# === simplify blocks report ===
	if 'summary' in exports:
		blocks = dict()
		with open('generated/reports/blocks.json', 'r') as f:
			for key, data in json.load(f).items():
				properties = data.get('properties')
				if properties:
					default = next(s.get('properties') for s in data['states'] if s.get('default'))
					blocks[key.removeprefix('minecraft:')] = (properties, default)

	# === read commands report ===
	if 'summary' in exports:
		with open('generated/reports/commands.json', 'r') as f:
			commands = json.load(f)

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

	if 'summary' in exports:
		export(dict(sorted(registries.items())), 'summary/registries')
		export(dict(sorted(blocks.items())), 'summary/blocks')
		export(commands, 'summary/commands')
		export(version_meta, 'summary/version')

	# === export data ===
	if 'data' in exports:
		with open(f'data/version.json', 'w') as f:
			json.dump(version_meta, f, indent=2)
			f.write('\n')


def init_output(date: str, exports: tuple[str]):
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
