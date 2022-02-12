# mcmeta
> Processed, version controlled history of Minecraft's generated data and assets

## Repository structure
Each of the following branches has a commit per version. Starting from 1.14, updated to the latest snapshot. Each commit is tagged `<version>-<branch>`.

* [**summary**](https://github.com/misode/mcmeta/tree/summary) - Branch with condensed reports from the data generator or assets, in a variety of formats.
  * [**blocks**](https://github.com/misode/mcmeta/blob/summary/blocks/data.json) - Containing block state properties and defaults for all necessary blocks.
  * [**commands**](https://github.com/misode/mcmeta/blob/summary/commands/data.json) - The brigadier command tree.
  * [**registries**](https://github.com/misode/mcmeta/blob/summary/registries/data.json) - Collections of resource locations. Including the generated registries, data, and assets.
  * [**sounds**](https://github.com/misode/mcmeta/blob/summary/sounds/data.json) - The sounds.json from assets.
  * [**versions**](https://github.com/misode/mcmeta/blob/summary/versions/data.json) - A list of versions up to that point ordered with the most recent first. Each entry has the same format as the `version.json` at the root of each branch.
* [**data**](https://github.com/misode/mcmeta/tree/data) - The vanilla data as it if would appear in a data pack.
* [**data-json**](https://github.com/misode/mcmeta/tree/data-json) - The same as **data** but only containing json files, so excluding structures.
* [**assets**](https://github.com/misode/mcmeta/tree/assets) - The vanilla assets is if they would appear in a resource pack.
* [**assets-json**](https://github.com/misode/mcmeta/tree/assets-json) - The same as **assets** but only containing json files, so excluding textures, sounds and shaders.
* [**atlas**](https://github.com/misode/mcmeta/tree/atlas) - Texture atlases of blocks, items and entities

## Sources
* [Version manifest](https://launchermeta.mojang.com/mc/game/version_manifest_v2.json), a list of versions and metadata, client and server jars by following links
* Sound files from Mojang's API following the version manifest
* Data generator using the following command:
  ```sh
  java -cp server.jar net.minecraft.data.Main --server --reports
  ```
* Slicedlime's [examples repo](https://github.com/slicedlime/examples) for worldgen changes before 1.18-pre1

## Credits
This project has taken inspiration from [Arcensoth/mcdata](https://github.com/Arcensoth/mcdata) and [SPGoding/vanilla-datapack](https://github.com/SPGoding/vanilla-datapack).

## Disclaimer
*mcmeta is not an official Minecraft product, and is not endorsed by or associated with Mojang Studios. All data and assets were obtained through Mojang's internal data generator and public API. If Mojang ever has something against this data existing here, the repository will be promptly removed.*
