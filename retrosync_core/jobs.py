import copy
import glob
import json
import logging
import re
import tempfile
from collections import defaultdict
from pathlib import Path

from lxml import etree

logger = logging.getLogger()

item_tpl = {
    "path": "",
    "label": "",
    "core_path": "DETECT",
    "core_name": "DETECT",
    "crc32": "00000000|crc",
    "db_name": "",
}


class JobBase:
    pass


class GlobalJob(JobBase):
    def __init__(self, default, playlists, transport):
        self.default = default
        self.playlists = playlists
        self.transport = transport
        self.size = 1
        self.transfer_bytes = 0
        self.setup()

    def setup(self):
        pass


class BiosSync(GlobalJob):
    name = "BIOS"

    def setup(self):
        self.src = Path(self.default.get("src_bios"))
        self.dst = Path(self.default.get("dest_bios"))
        self.size = self.transport.guess_file_count(self.src, [], True)
        self.transfer_bytes = self.transport.guess_total_size(self.src, [], True)

    def do(self, callback=None):
        self.transport.copy_files(
            self.src,
            self.dst,
            whitelist=[],
            recursive=True,
            callback=callback,
        )


class ThumbnailsSync(BiosSync):
    name = "Thumbnails"

    def setup(self):
        self.src = Path(self.default.get("src_thumbnails"))
        self.dst = Path(self.default.get("dest_thumbnails"))
        self.size = self.transport.guess_file_count(self.src, [], True)
        self.transfer_bytes = self.transport.guess_total_size(self.src, [], True)


class FavoritesSync(BiosSync):
    name = "Favorites"

    def setup(self):
        self.src = Path(self.default.get("src_config")) / "content_favorites.lpl"
        self.dst = Path(self.default.get("dest_config")) / "content_favorites.lpl"
        self.size = 1
        self.transfer_bytes = self.src.stat().st_size if self.src.exists() else 0

    def do(self, callback=None):
        with tempfile.NamedTemporaryFile() as temp_file:
            self.migrate(
                self.src,
                temp_file,
            )
            self.transport.copy_file(
                Path(temp_file.name),
                self.dst,
            )
            if callback:
                callback()

    def migrate(self, favorites_file, temp_file):
        def find_playlist(playlists, src_core_name):
            for p in playlists:
                if p.get("src_core_name") == src_core_name:
                    return p
            print(f"Can not find core {src_core_name}")
            raise AssertionError()

        logger.debug(f"migrate: filename={favorites_file}")
        with open(favorites_file) as file:
            data = json.load(file)

        items = []
        src_items = data["items"]
        src_items_len = len(src_items)
        for idx, item in enumerate(src_items):
            new_item = copy.copy(item)
            playlist = find_playlist(self.playlists, new_item["core_name"])
            dest_rom_dir = Path(self.default.get("target_roms")) / playlist.get("dest_folder")
            src_path = new_item["path"].split("#")[0]
            src_name = Path(src_path).name
            new_path = dest_rom_dir / src_name
            new_item["path"] = str(new_path)
            core_path = (
                new_item["core_path"]
                .replace(
                    self.default.get("src_cores_suffix"), self.default.get("target_cores_suffix")
                )
                .replace(self.default.get("src_cores"), self.default.get("target_cores"))
            )
            new_item["core_path"] = core_path
            logger.debug(f"migrate: Convert [{idx + 1}/{src_items_len}] path={src_name}")
            items.append(new_item)

        data["items"] = items
        doc = json.dumps(data)
        logger.debug(json.dumps(data, indent=2))
        temp_file.write(doc.encode("utf-8"))
        temp_file.flush()
        temp_file.seek(0)


class SystemJob(JobBase):
    def __init__(self, default, transport):
        self.default = default
        self.transport = transport
        self.size = 1
        self.transfer_bytes = 0

    def get_src_rom_roots(self):
        src_roms = self.default.get("src_roms")
        if isinstance(src_roms, list):
            roots = [Path(item) for item in src_roms]
        else:
            roots = [Path(src_roms)]
        return roots

    def get_primary_src_rom_root(self):
        roots = self.get_src_rom_roots()
        if not roots:
            raise AssertionError("No source ROM directories configured")
        return roots[0]


class RomSyncJob(SystemJob):
    name = "Sync ROMs"

    def setup(self, playlist):
        self.playlist = playlist
        self.src = self.get_primary_src_rom_root() / self.playlist.get("src_folder")
        self.dst = Path(self.default.get("dest_roms")) / self.playlist.get("dest_folder")
        self.size = self.transport.guess_file_count(self.src, [], True)
        self.transfer_bytes = self.transport.guess_total_size(self.src, [], True)

    def do(self, callback):
        self.transport.copy_files(
            self.src, self.dst, whitelist=[], recursive=True, callback=callback
        )


class PlaylistSyncJob(SystemJob):
    name = "Sync Playlist"

    def setup(self, playlist):
        self.playlist = playlist
        self.size = 1
        local = Path(self.default.get("src_playlists")) / self.playlist.get("name")
        self.transfer_bytes = local.stat().st_size if local.exists() else 0

    def migrate_playlist(self, temp_file):
        name = self.playlist.get("name")
        logger.debug(f"migrate_playlist: name={name}")
        local = Path(self.default.get("src_playlists")) / name
        with open(local) as file:
            data = json.load(file)

        core_path = (
            data["default_core_path"]
            .replace(self.default.get("src_cores_suffix"), self.default.get("target_cores_suffix"))
            .replace(self.default.get("src_cores"), self.default.get("target_cores"))
        )
        src_rom_dirs = [root / self.playlist.get("src_folder") for root in self.get_src_rom_roots()]
        target_rom_dir = Path(self.default.get("target_roms")) / self.playlist.get("dest_folder")
        data["default_core_path"] = core_path
        data["scan_content_dir"] = str(target_rom_dir)
        data["scan_dat_file_path"] = ""

        items = []
        src_items = data["items"]
        src_items_len = len(src_items)
        for idx, item in enumerate(src_items):
            new_item = copy.copy(item)
            new_item["core_name"] = "DETECT"
            new_item["core_path"] = "DETECT"
            src_path = new_item["path"].split("#")[0]
            src_name = Path(src_path).name
            logger.debug(f"migrate_playlist: Convert [{idx + 1}/{src_items_len}] path={src_name}")
            new_path = src_path
            for src_rom_dir in src_rom_dirs:
                new_path = new_path.replace(str(src_rom_dir), str(target_rom_dir))
            new_item["path"] = new_path
            items.append(new_item)

        data["items"] = items
        doc = json.dumps(data)
        logger.debug(json.dumps(data, indent=2))
        temp_file.write(doc.encode("utf-8"))
        temp_file.flush()
        temp_file.seek(0)

    def do(self, callback=None):
        name = self.playlist.get("name")
        with tempfile.NamedTemporaryFile() as temp_file:
            self.migrate_playlist(temp_file)
            self.transport.copy_file(
                Path(temp_file.name), Path(self.default.get("dest_playlists")) / name
            )
        if callback:
            callback()


class PlaylistUpdateJob(SystemJob):
    name = "Update Playlist"

    def setup(self, playlist):
        self.playlist = playlist
        self.size = 1

    def backup_file(self, file_path):
        original_file = Path(file_path)
        backup_file = original_file.with_suffix(original_file.suffix + ".backup")
        backup_file.write_bytes(original_file.read_bytes())
        logger.debug(f"backup_file: created {backup_file}")
        return str(backup_file)

    def make_item(self, local, file):
        stem = str(Path(file).stem)
        new_item = copy.copy(item_tpl)
        new_item["path"] = file
        new_item["label"] = self.name_map.get(stem, stem)
        new_item["db_name"] = local.name
        return new_item

    def create_m3u(self, src_rom_dir):
        logger.debug("create_m3u: Create m3u files")
        m3u_pattern = self.playlist.get("src_m3u_pattern")
        m3u_whitelist = self.playlist.get("src_m3u_whitelist")
        files = defaultdict(list)
        all_files = Path(src_rom_dir)
        for filename in all_files.iterdir():
            if re.compile(m3u_whitelist).search(str(filename)):
                e = re.compile(m3u_pattern)
                m = e.match(str(filename))
                if m:
                    base_name = m.groups()[0].strip()
                else:
                    base_name = filename.stem
                files[base_name].append(filename)
        for base_name, list_files in files.items():
            m3u_file = Path(src_rom_dir) / f"{base_name}.m3u"
            if not self.transport.dry_run:
                with open(m3u_file, "w") as f:
                    logger.debug(f"create_m3u: Create  {str(m3u_file)}")
                    for filename in sorted(list_files):
                        f.write(f"{filename.name}\n")

    def build_file_map(self, src_rom_dir, dat_file):
        name_map = {}
        if not dat_file:
            return name_map
        dat_file = src_rom_dir / dat_file
        with open(dat_file) as fd:
            data = fd.read()
        root = etree.fromstring(data)
        for game in root.xpath("//game"):
            description = game.findtext("description")
            if description:
                name_map[game.attrib["name"]] = description
                continue
            if game.attrib.get("parent"):
                continue
            identity = game.findall("identity")
            title = identity[0].findtext("title")
            if title:
                name_map[game.attrib["name"]] = title
        return name_map

    def do(self, callback=None):
        name = self.playlist.get("name")
        logger.debug(f"migrate_playlist: name={name}")
        local = Path(self.default.get("src_playlists")) / name
        if not self.transport.dry_run:
            self.backup_file(local)

        with open(local) as file:
            data = json.load(file)

        src_rom_dir = self.get_primary_src_rom_root() / self.playlist.get("src_folder")

        core_path = Path(self.default.get("src_cores")) / self.playlist.get("src_core_path")
        core_path = core_path.with_suffix(self.default.get("src_cores_suffix"))
        data["default_core_path"] = str(core_path)
        data["default_core_name"] = self.playlist.get("src_core_name")
        data["scan_content_dir"] = str(src_rom_dir)
        data["scan_dat_file_path"] = str(src_rom_dir)

        if self.playlist.get("src_create_m3u"):
            self.create_m3u(src_rom_dir)

        whitelist = self.playlist.get("src_whitelist", False)
        blacklist = self.playlist.get("src_blacklist", False)
        self.name_map = self.build_file_map(src_rom_dir, self.playlist.get("src_dat_file", ""))
        items = []
        files = glob.glob(str(src_rom_dir / "*"))
        files.sort()
        files_len = len(files)

        file_list = []
        for idx, file in enumerate(files):
            logger.debug(
                f"update_playlist: Update first pass [{idx + 1}/{files_len}] path={Path(file).name}"
            )
            if Path(file).is_dir():
                subs = glob.glob(str(Path(file) / "*"))
                for sub in subs:
                    file_list.append(sub)
            else:
                file_list.append(file)

        files_len = len(file_list)
        for idx, file in enumerate(file_list):
            logger.debug(
                f"update_playlist: Update second pass [{idx + 1}/{files_len}] path={Path(file).name}"
            )

            if blacklist:
                if re.compile(blacklist).search(file):
                    logger.debug(f"update_playlist: Skip {Path(file).name} is blacklisted")
                    continue

            if whitelist:
                if re.compile(whitelist).search(file):
                    logger.debug(f"update_playlist: Add {Path(file).name} is whitelisted")
                    items.append(self.make_item(local, file))
            else:
                items.append(self.make_item(local, file))

        data["items"] = items
        doc = json.dumps(data, indent=2)
        logger.debug(json.dumps(data, indent=2))
        if not self.transport.dry_run:
            with open(str(local), "w") as new_file:
                new_file.write(doc)
        if callback:
            callback()


class PlaylistUpdatecJob(PlaylistUpdateJob):
    pass
