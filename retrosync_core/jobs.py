import copy
import glob
import json
import logging
import re
import tempfile
import time
from collections import defaultdict
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse
from urllib.request import urlopen

import Levenshtein
from lxml import etree
from lxml import html
from rich.console import Console
from rich.table import Table

from .transports import TransportError

logger = logging.getLogger()

item_tpl = {
    "path": "",
    "label": "",
    "core_path": "DETECT",
    "core_name": "DETECT",
    "crc32": "00000000|crc",
    "db_name": "",
}


@dataclass(frozen=True)
class TitleFeatures:
    raw: str
    exact: str
    normalized: str
    relaxed: str
    canonical: str
    tokens: frozenset[str]
    numeric_tokens: frozenset[str]
    region_tokens: frozenset[str]
    release_flags: frozenset[str]


class JobBase:
    pass


class GlobalJob(JobBase):
    def __init__(self, default, playlists, transport):
        self.default = default
        self.playlists = playlists
        self.transport = transport
        self._deferred_messages = []
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

    def do(self, callback=None, cancel_check=None):
        kwargs = {
            "whitelist": [],
            "recursive": True,
            "callback": callback,
        }
        if cancel_check is not None:
            kwargs["cancel_check"] = cancel_check
        self.transport.copy_files(self.src, self.dst, **kwargs)


class ThumbnailsSync(JobBase):
    name = "Thumbnails"

    def __init__(self, default, transport):
        self.default = default
        self.transport = transport
        self._deferred_messages = []
        self._final_deferred_messages = []
        self.size = 0
        self.transfer_bytes = 0

    def setup(self, playlist):
        self.playlist = playlist
        system_name = Path(self.playlist.get("name", "")).stem
        self.src = Path(self.default.get("src_thumbnails")) / system_name
        self.dst = Path(self.default.get("dest_thumbnails")) / system_name
        if not self.src.is_dir():
            self.size = 0
            self.transfer_bytes = 0
            return
        self.size = self.transport.guess_file_count(self.src, [], True)
        self.transfer_bytes = self.transport.guess_total_size(self.src, [], True)

    def do(self, callback=None, cancel_check=None):
        if not self.src.is_dir():
            return
        kwargs = {
            "whitelist": [],
            "recursive": True,
            "callback": callback,
        }
        if cancel_check is not None:
            kwargs["cancel_check"] = cancel_check
        self.transport.copy_files(self.src, self.dst, **kwargs)


class FavoritesSync(BiosSync):
    name = "Favorites"

    def setup(self):
        self.src = Path(self.default.get("src_config")) / "content_favorites.lpl"
        self.dst = Path(self.default.get("dest_config")) / "content_favorites.lpl"
        self.size = 1
        self.transfer_bytes = self.src.stat().st_size if self.src.exists() else 0

    def do(self, callback=None, cancel_check=None):
        with tempfile.NamedTemporaryFile() as temp_file:
            self.migrate(
                self.src,
                temp_file,
            )
            kwargs = {}
            if cancel_check is not None:
                kwargs["cancel_check"] = cancel_check
            self.transport.copy_file(Path(temp_file.name), self.dst, **kwargs)
            if callback:
                callback()

    def migrate(self, favorites_file, temp_file):
        def find_playlist(playlists, src_core_name):
            for p in playlists:
                if p.get("src_core_name") == src_core_name:
                    return p
            raise TransportError(
                f"Cannot find playlist mapping for core '{src_core_name}' while migrating favorites."
            )

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
        self._deferred_messages = []
        self._final_deferred_messages = []
        self.size = 1
        self.transfer_bytes = 0

    def add_deferred_message(self, message):
        if message:
            self._deferred_messages.append(str(message))

    def consume_deferred_messages(self):
        messages = list(self._deferred_messages)
        self._deferred_messages.clear()
        return messages

    def add_final_deferred_message(self, message):
        if message:
            self._final_deferred_messages.append(str(message))

    def consume_final_deferred_messages(self):
        messages = list(self._final_deferred_messages)
        self._final_deferred_messages.clear()
        return messages

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

    def build_preview_rows(self):
        return []


class RomSyncJob(SystemJob):
    name = "Sync ROMs"

    def setup(self, playlist):
        self.playlist = playlist
        self.src = self.get_primary_src_rom_root() / self.playlist.get("src_folder")
        self.dst = Path(self.default.get("dest_roms")) / self.playlist.get("dest_folder")
        self.size = self.transport.guess_file_count(self.src, [], True)
        self.transfer_bytes = self.transport.guess_total_size(self.src, [], True)

    def do(self, callback=None, cancel_check=None):
        kwargs = {
            "whitelist": [],
            "recursive": True,
            "callback": callback,
        }
        if cancel_check is not None:
            kwargs["cancel_check"] = cancel_check
        self.transport.copy_files(self.src, self.dst, **kwargs)


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

    def do(self, callback=None, cancel_check=None):
        name = self.playlist.get("name")
        with tempfile.NamedTemporaryFile() as temp_file:
            self.migrate_playlist(temp_file)
            kwargs = {}
            if cancel_check is not None:
                kwargs["cancel_check"] = cancel_check
            self.transport.copy_file(
                Path(temp_file.name), Path(self.default.get("dest_playlists")) / name, **kwargs
            )
        if callback:
            callback()


class PlaylistUpdateJob(SystemJob):
    name = "Update Playlist"
    REGION_PRIORITY = {
        "usa": 1,
        "us": 1,
        "ntsc": 1,
        "world": 2,
        "europe": 3,
        "pal": 3,
        "japan": 4,
        "jp": 4,
        "australia": 5,
    }
    QUAKE_PARENT_LABELS = {
        "ID - Quake": {
            "id1": "Quake",
            "hipnotic": "Quake Mission Pack No. 1: Scourge of Armagon",
            "rogue": "Quake Mission Pack No. 2: Dissolution of Eternity",
        },
        "ID - Quake2": {
            "baseq2": "Quake II",
            "ctf": "Quake II",
            "rogue": "Quake II Mission Pack: Ground Zero",
            "xatrix": "Quake II Mission Pack: The Reckoning",
            "zaero": "Quake II Mission Pack: The Zaero Mission Pack",
        },
        "ID - Quake3": {
            "baseq3": "Quake III Arena",
            "missionpack": "Quake III: Team Arena",
            "baseoa": "OpenArena",
        },
    }
    GLOBAL_TITLE_EQUIVALENTS = {
        "bubsy fractured furry tails": "bubsy in fractured furry tales",
        "doom evil unleashed": "doom",
        "double dragon v": "double dragon v shadow falls",
        "flashback": "flashback quest for identity",
        "nba jam tournament edition": "nba jam tournament edition",
        "val disere skiing snowboarding": "val disere skiing and snowboarding",
    }
    SYSTEM_TITLE_EQUIVALENTS = {
        "Sega - 32X": {
            "after burner 32x": "after burner complete",
            "brutal unleashed 32x": "brutal above the claw",
            "doom 32x": "doom",
            "pitfall 32x": "pitfall mayan adventure",
            "shadow squadron 32x": "stellar assault",
        }
    }

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
        default_label = self.name_map.get(stem, stem)
        default_label = self.resolve_special_playlist_label(file, default_label)
        new_item["label"] = self.resolve_thumbnail_label(stem, default_label)
        new_item["db_name"] = local.name
        return new_item

    def resolve_special_playlist_label(self, file, default_label):
        path = Path(file)
        stem = path.stem.lower()
        if not re.match(r"^(pak\d+[a-z0-9-]*|mp-pak\d+|q3wpak\d+)$", stem):
            return default_label

        src_folder = self.playlist.get("src_folder")
        parent_labels = self.QUAKE_PARENT_LABELS.get(src_folder)
        if not parent_labels:
            return default_label

        parent_name = path.parent.name.lower()
        return parent_labels.get(
            parent_name, Path(self.playlist.get("name", "")).stem or default_label
        )

    def _normalize_thumbnail_key(self, name):
        normalized = name.lower().strip()
        normalized = re.sub(r"\.[a-z0-9]{1,5}$", "", normalized)
        # Remove common release metadata tokens that often differ from thumbnail naming.
        normalized = re.sub(
            r"\((usa|us|europe|eu|japan|jp|world|pal|ntsc|prototype|proto|beta)[^)]*\)",
            "",
            normalized,
        )
        normalized = re.sub(
            r"\((rev[^)]*|v\d+(\.\d+)?|disc \d+|disk \d+|demo|sample)[^)]*\)", "", normalized
        )
        normalized = re.sub(r"\[[^\]]*\]", "", normalized)
        normalized = re.sub(r"[_\-.:/]+", " ", normalized)
        normalized = re.sub(r"\s+", " ", normalized).strip()
        return normalized

    def _relaxed_thumbnail_key(self, name):
        normalized = name.lower().strip()
        normalized = re.sub(r"\.[a-z0-9]{1,5}$", "", normalized)
        normalized = re.sub(r"\([^)]*\)", "", normalized)
        normalized = re.sub(r"\[[^\]]*\]", "", normalized)
        normalized = re.sub(r"[_\-.:/]+", " ", normalized)
        normalized = re.sub(r"\s+", " ", normalized).strip()
        return normalized

    def _canonicalize_title_variants(self, name):
        canonical = self._relaxed_thumbnail_key(name)
        canonical = re.sub(r"[!:'\",?&]+", "", canonical)
        canonical = re.sub(r"\bthe\s+", "", canonical)
        canonical = re.sub(r"\bjunior\b", "jr", canonical)
        canonical = re.sub(r"\bbros\b", "brothers", canonical)
        canonical = re.sub(r"\bvs\b", "versus", canonical)
        canonical = re.sub(r"\bkung fu\b", "kungfu", canonical)
        canonical = re.sub(r"\bte\b", "tournament edition", canonical)
        canonical = re.sub(r"\bf\s*14\b", "f14", canonical)
        canonical = re.sub(r"\buh\s*ix\b", "uhix", canonical)
        canonical = re.sub(r"\s+", " ", canonical).strip()
        canonical = self.GLOBAL_TITLE_EQUIVALENTS.get(canonical, canonical)
        playlist = getattr(self, "playlist", {}) or {}
        system_name = Path(playlist.get("name", "")).stem
        system_equivalents = self.SYSTEM_TITLE_EQUIVALENTS.get(system_name, {})
        return system_equivalents.get(canonical, canonical)

    def _extract_region_tokens(self, name):
        tokens = set()
        lowered = name.lower()
        region_aliases = {
            "pal": {"europe", "pal"},
            "europe": {"europe", "pal"},
            "eu": {"europe", "pal"},
            "euro": {"europe", "pal"},
            "usa": {"usa", "us", "ntsc"},
            "us": {"usa", "us", "ntsc"},
            "ntsc": {"usa", "us", "ntsc"},
            "japan": {"japan", "jp"},
            "jp": {"japan", "jp"},
            "world": {"world"},
            "australia": {"australia"},
        }
        for raw_token in re.findall(r"\(([^)]*)\)", lowered):
            parts = re.split(r"[^a-z0-9]+", raw_token)
            for part in parts:
                if part in region_aliases:
                    tokens.update(region_aliases[part])
        return tokens

    def _extract_release_flags(self, name):
        lowered = name.lower()
        flags = set()
        for marker in ["beta", "demo", "proto", "prototype", "sample"]:
            if marker in lowered:
                flags.add(marker)
        if "rev " in lowered or "revision" in lowered:
            flags.add("revision")
        return flags

    def _tokenize_title(self, canonical):
        if not canonical:
            return frozenset()
        return frozenset(token for token in canonical.split() if token)

    def _extract_numeric_tokens(self, name):
        tokens = set()
        for raw_token in re.findall(r"\(([^)]*)\)", name):
            tokens.update(re.findall(r"\b\d+\b", raw_token))
        for raw_token in re.findall(r"\[([^\]]*)\]", name):
            tokens.update(re.findall(r"\b\d+\b", raw_token))
        return frozenset(tokens)

    def parse_title_features(self, name):
        normalized = self._normalize_thumbnail_key(name)
        relaxed = self._relaxed_thumbnail_key(name)
        canonical = self._canonicalize_title_variants(name)
        return TitleFeatures(
            raw=name,
            exact=name.casefold(),
            normalized=normalized,
            relaxed=relaxed,
            canonical=canonical,
            tokens=self._tokenize_title(canonical),
            numeric_tokens=self._extract_numeric_tokens(name),
            region_tokens=frozenset(self._extract_region_tokens(name)),
            release_flags=frozenset(self._extract_release_flags(name)),
        )

    def _release_penalty(self, name):
        lowered = name.lower()
        penalty = 0
        for marker, weight in {
            "beta": 40,
            "proto": 35,
            "prototype": 35,
            "demo": 30,
            "sample": 25,
            "rev ": 10,
            "revision": 10,
            "ces": 15,
            "wces": 15,
        }.items():
            if marker in lowered:
                penalty += weight
        return penalty

    def _release_rank(self, name):
        lowered = name.lower()
        if any(marker in lowered for marker in ["beta", "proto", "prototype", "demo", "sample"]):
            return 3
        if "rev " in lowered or "revision" in lowered:
            return 2
        return 1

    def _metadata_penalty(self, name):
        lowered = name.lower()
        penalty = (len(re.findall(r"\([^)]*\)", name)) + len(re.findall(r"\[[^\]]*\]", name))) * 3
        for marker, weight in {
            "set ": 8,
            "alt": 8,
            "demo": 12,
            "hack": 15,
            "patched": 10,
            "patch": 8,
            "logo": 8,
            "with ": 6,
            "freeware": 12,
            "sample": 10,
            "test": 8,
        }.items():
            if marker in lowered:
                penalty += weight
        return penalty

    def _candidate_group_key(self, features):
        numeric_tail = tuple(sorted(token for token in features.numeric_tokens if len(token) >= 3))
        return (
            features.canonical,
            tuple(sorted(features.region_tokens)),
            numeric_tail,
        )

    def _region_priority(self, region_tokens):
        if not region_tokens:
            return 999
        return min(self.REGION_PRIORITY.get(token, 999) for token in region_tokens)

    def _select_best_with_tiebreak(self, current, challenger):
        if current is None:
            return challenger
        if challenger is None:
            return current
        if challenger[0] > current[0]:
            return challenger
        if challenger[0] < current[0]:
            return current
        if challenger[5] < current[5]:
            return challenger
        if challenger[5] > current[5]:
            return current
        if challenger[6] < current[6]:
            return challenger
        if challenger[6] > current[6]:
            return current
        if challenger[7] < current[7]:
            return challenger
        if challenger[7] > current[7]:
            return current
        if challenger[1] < current[1]:
            return challenger
        return current

    def build_thumbnail_index_from_names(self, names, *, url_map=None):
        index = {
            "exact": {},
            "normalized": {},
            "relaxed": {},
            "canonical": {},
            "features": {},
            "urls": url_map or {},
            "names": [],
        }
        seen = set()
        for base in names:
            if not base or base in seen:
                continue
            seen.add(base)
            index["names"].append(base)
            features = self.parse_title_features(base)
            index["features"][base] = features
            exact_key = base.casefold()
            if exact_key not in index["exact"]:
                index["exact"][exact_key] = base
            if features.normalized:
                index["normalized"].setdefault(features.normalized, set()).add(base)
            if features.relaxed:
                index["relaxed"].setdefault(features.relaxed, set()).add(base)
            if features.canonical:
                index["canonical"].setdefault(features.canonical, set()).add(base)
        return index

    def build_thumbnail_index(self):
        src_thumbnails = self.default.get("src_thumbnails")
        if not src_thumbnails:
            return self.build_thumbnail_index_from_names([])

        system_name = Path(self.playlist.get("name")).stem
        system_dir = Path(src_thumbnails) / system_name
        if not system_dir.is_dir():
            return self.build_thumbnail_index_from_names([])

        thumb_folders = [
            "Named_Boxarts",
            "Named_Snaps",
            "Named_Titles",
        ]
        names = []
        for folder in thumb_folders:
            path = system_dir / folder
            if not path.is_dir():
                continue
            for thumb in path.iterdir():
                if not thumb.is_file():
                    continue
                names.append(thumb.stem)
        return self.build_thumbnail_index_from_names(names)

    def _score_title_pair(self, source, target):
        score = 0.0
        match_type = "fuzzy"
        region_used = False

        if source.exact == target.exact:
            score += 120
            match_type = "exact"
        elif source.normalized and source.normalized == target.normalized:
            score += 100
            match_type = "normalized"
        elif source.relaxed and source.relaxed == target.relaxed:
            score += 90
            match_type = "relaxed"
        elif source.canonical and source.canonical == target.canonical:
            score += 85
            match_type = "canonical"
        else:
            if source.tokens and target.tokens:
                overlap = len(source.tokens & target.tokens)
                union = len(source.tokens | target.tokens)
                ratio = overlap / max(union, 1)
                score += ratio * 60
                if ratio >= 0.75:
                    score += 12
                elif ratio >= 0.5:
                    score += 6

                src_joined = " ".join(sorted(source.tokens))
                tgt_joined = " ".join(sorted(target.tokens))
                fuzzy_ratio = Levenshtein.ratio(src_joined, tgt_joined)
                score += fuzzy_ratio * 25

        if source.numeric_tokens and target.numeric_tokens:
            overlap = len(source.numeric_tokens & target.numeric_tokens)
            if overlap:
                score += overlap * 18
            missing = len(source.numeric_tokens - target.numeric_tokens)
            extra = len(target.numeric_tokens - source.numeric_tokens)
            score -= missing * 10
            score -= extra * 4

        if source.region_tokens and target.region_tokens:
            if source.region_tokens & target.region_tokens:
                score += 15
                region_used = True
            else:
                score -= 20
        elif "world" in target.region_tokens:
            score += 4

        if source.release_flags & target.release_flags:
            score += 6
        elif source.release_flags and target.release_flags:
            score -= 6

        if target.release_flags and not (source.release_flags & target.release_flags):
            score -= self._release_penalty(target.raw)

        if region_used and match_type in {"normalized", "relaxed", "canonical"}:
            match_type = f"{match_type}-region"

        if match_type == "fuzzy" and score < 55:
            return None

        return {
            "matched": target.raw,
            "match_type": match_type,
            "score_value": score,
            "release_rank": self._release_rank(target.raw),
            "region_priority": self._region_priority(target.region_tokens),
            "metadata_penalty": self._metadata_penalty(target.raw),
        }

    def _build_match_result(self, thumb_name, candidate, target_features, score_value, match_type):
        return {
            "matched": thumb_name,
            "match_type": match_type,
            "score": 1.0 if score_value >= 80 else round(min(score_value / 80, 1.0), 3),
            "candidate": candidate,
            "url": self.thumbnail_index["urls"].get(thumb_name),
            "_target_features": target_features,
            "_source_features": None,
            "_score_value": score_value,
            "_release_rank": self._release_rank(target_features.raw),
            "_region_priority": self._region_priority(target_features.region_tokens),
            "_metadata_penalty": self._metadata_penalty(target_features.raw),
        }

    def _fast_index_match_for_candidate(self, candidate, source_features):
        index = self.thumbnail_index
        exact_match = index["exact"].get(source_features.exact)
        if exact_match:
            target_features = index["features"][exact_match]
            return self._build_match_result(exact_match, candidate, target_features, 120, "exact")

        for key_name, match_type, score_value in [
            ("normalized", "normalized", 100),
            ("relaxed", "relaxed", 90),
            ("canonical", "canonical", 85),
        ]:
            lookup_key = getattr(source_features, key_name)
            if not lookup_key:
                continue
            matches = index[key_name].get(lookup_key)
            if not matches or len(matches) != 1:
                continue
            thumb_name = next(iter(matches))
            target_features = index["features"][thumb_name]
            if (
                key_name == "normalized"
                and source_features.region_tokens
                and target_features.region_tokens
            ):
                if source_features.region_tokens & target_features.region_tokens:
                    return self._build_match_result(
                        thumb_name,
                        candidate,
                        target_features,
                        115,
                        "normalized-region",
                    )
                continue
            if key_name in {"relaxed", "canonical"} and source_features.region_tokens:
                if target_features.region_tokens:
                    if source_features.region_tokens & target_features.region_tokens:
                        return self._build_match_result(
                            thumb_name,
                            candidate,
                            target_features,
                            score_value + 15,
                            f"{match_type}-region",
                        )
                    continue
            return self._build_match_result(
                thumb_name, candidate, target_features, score_value, match_type
            )

        return None

    def _cleanup_match_result(self, result):
        if result is None:
            return None
        cleaned = dict(result)
        for key in [
            "_target_features",
            "_source_features",
            "_score_value",
            "_release_rank",
            "_region_priority",
            "_metadata_penalty",
        ]:
            cleaned.pop(key, None)
        return cleaned

    def match_thumbnail_candidate(
        self,
        stem,
        default_label,
        *,
        allow_fuzzy=False,
        source_feature_map=None,
    ):
        best = None
        second_best = None
        best_group = None
        seen_candidates = []
        for candidate in [default_label, stem]:
            if candidate not in seen_candidates:
                seen_candidates.append(candidate)

        for candidate in seen_candidates:
            if source_feature_map and candidate in source_feature_map:
                source_features = source_feature_map[candidate]
            else:
                source_features = self.parse_title_features(candidate)

            fast_match = self._fast_index_match_for_candidate(candidate, source_features)
            if fast_match is not None:
                fast_match["_source_features"] = source_features
                return self._cleanup_match_result(fast_match)

            for thumb_name in self.thumbnail_index["names"]:
                target_features = self.thumbnail_index["features"][thumb_name]
                scored = self._score_title_pair(source_features, target_features)
                if scored is None:
                    continue
                if not allow_fuzzy and scored["match_type"] == "fuzzy":
                    continue
                group_key = self._candidate_group_key(target_features)
                item = (
                    scored["score_value"],
                    thumb_name,
                    candidate,
                    scored["match_type"],
                    group_key,
                    scored["release_rank"],
                    scored["region_priority"],
                    scored["metadata_penalty"],
                )
                preferred = self._select_best_with_tiebreak(best, item)
                if preferred is item:
                    if best is not None and best[4] != item[4]:
                        second_best = best
                    best = item
                    best_group = item[4]
                elif item[4] != best_group:
                    second_best = self._select_best_with_tiebreak(second_best, item)

        if best is None:
            return None

        min_score = 55 if allow_fuzzy else 80
        second_score = second_best[0] if second_best is not None else float("-inf")
        min_margin = 8 if allow_fuzzy else 3
        if best[0] < min_score:
            return None
        if (best[0] - second_score) < min_margin:
            best_release_rank = best[5]
            second_release_rank = second_best[5] if second_best is not None else None
            if second_best is None or best_release_rank < second_release_rank:
                pass
            elif best_release_rank == 2 and second_release_rank == 2:
                tied_revision_candidates = sorted(
                    item[1]
                    for item in [best, second_best]
                    if item is not None and self._release_rank(item[1]) == 2
                )
                if tied_revision_candidates and best[1] != tied_revision_candidates[0]:
                    best = next(
                        item
                        for item in [best, second_best]
                        if item and item[1] == tied_revision_candidates[0]
                    )
            elif best_release_rank == second_release_rank:
                source_regions = (
                    source_feature_map.get(best[2]).region_tokens
                    if source_feature_map and best[2] in source_feature_map
                    else self.parse_title_features(best[2]).region_tokens
                )
                if not source_regions:
                    best_region_priority = best[6]
                    second_region_priority = second_best[6] if second_best is not None else None
                    if second_best is None or best_region_priority < second_region_priority:
                        pass
                    elif best_region_priority == second_region_priority:
                        best_metadata_penalty = best[7]
                        second_metadata_penalty = (
                            second_best[7] if second_best is not None else None
                        )
                        if (
                            second_best is None
                            or second_metadata_penalty is None
                            or best_metadata_penalty < second_metadata_penalty
                        ):
                            pass
                        else:
                            return None
                    else:
                        return None
                else:
                    return None
            else:
                return None

        return {
            "matched": best[1],
            "match_type": best[3],
            "score": 1.0 if best[0] >= 80 else round(min(best[0] / 80, 1.0), 3),
            "candidate": best[2],
            "url": self.thumbnail_index["urls"].get(best[1]),
        }

    def resolve_thumbnail_label(self, stem, default_label):
        mode = self.playlist.get(
            "thumbnail_label_mode",
            self.default.get("thumbnail_label_mode", "prefer-thumbnail"),
        )
        if mode != "prefer-thumbnail":
            return default_label

        match = self.match_thumbnail_candidate(stem, default_label, allow_fuzzy=False)
        if match:
            self.thumbnail_match_count += 1
            if match["matched"] != default_label:
                logger.debug(
                    "update_playlist: thumbnail label adapted system=%s stem=%s from=%s to=%s match=%s candidate=%s",
                    Path(self.playlist.get("name")).stem,
                    stem,
                    default_label,
                    match["matched"],
                    match["match_type"],
                    match["candidate"],
                )
            return match["matched"]

        self.thumbnail_miss_count += 1
        return default_label

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

    def _iter_candidate_files(self, src_rom_dir):
        files = glob.glob(str(src_rom_dir / "*"))
        files.sort()

        file_list = []
        for file in files:
            if Path(file).is_dir():
                file_list.extend(glob.glob(str(Path(file) / "*")))
            else:
                file_list.append(file)
        return file_list

    def build_preview_rows(self):
        name = self.playlist.get("name")
        local = Path(self.default.get("src_playlists")) / name
        src_rom_dir = self.get_primary_src_rom_root() / self.playlist.get("src_folder")

        whitelist = self.playlist.get("src_whitelist", False)
        blacklist = self.playlist.get("src_blacklist", False)
        self.name_map = self.build_file_map(src_rom_dir, self.playlist.get("src_dat_file", ""))
        self.thumbnail_index = self.build_thumbnail_index()
        self.thumbnail_match_count = 0
        self.thumbnail_miss_count = 0

        rows = []
        for file in self._iter_candidate_files(src_rom_dir):
            if blacklist and re.compile(blacklist).search(file):
                continue

            include = True
            if whitelist:
                include = bool(re.compile(whitelist).search(file))
            if not include:
                continue

            item = self.make_item(local, file)
            rows.append(
                {
                    "rom": Path(file).name,
                    "path": str(file),
                    "label": item.get("label", ""),
                    "playlist": str(local),
                    "thumbnail_match": item.get("label", "") != Path(file).stem,
                }
            )
        return rows

    def do(self, callback=None, cancel_check=None):
        name = self.playlist.get("name")
        logger.debug(f"migrate_playlist: name={name}")
        local = Path(self.default.get("src_playlists")) / name
        if cancel_check and cancel_check():
            raise TransportError("Transfer interrupted by user.")
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
        self.thumbnail_index = self.build_thumbnail_index()
        self.thumbnail_match_count = 0
        self.thumbnail_miss_count = 0
        items = []
        files = glob.glob(str(src_rom_dir / "*"))
        files.sort()
        files_len = len(files)

        file_list = []
        for idx, file in enumerate(files):
            if cancel_check and cancel_check():
                raise TransportError("Transfer interrupted by user.")
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
            if cancel_check and cancel_check():
                raise TransportError("Transfer interrupted by user.")
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
        logger.debug(
            "update_playlist: thumbnail label matching matched=%s missed=%s",
            self.thumbnail_match_count,
            self.thumbnail_miss_count,
        )
        doc = json.dumps(data, indent=2)
        logger.debug(json.dumps(data, indent=2))
        if not self.transport.dry_run:
            with open(str(local), "w") as new_file:
                new_file.write(doc)
        if callback:
            callback()


class ThumbnailsUpdateJob(PlaylistUpdateJob):
    name = "Update Thumbnails"
    LIBRETRO_THUMBNAIL_URL = "https://thumbnails.libretro.com/"
    DIRECTORY_CACHE_VERSION = 2
    DIRECTORY_CACHE_TTL_SECONDS = 24 * 60 * 60
    REPORT_TEXT_COLUMN_MIN_WIDTH = 18
    ASSET_FOLDERS = {
        "boxart": "Named_Boxarts",
        "snap": "Named_Snaps",
        "title": "Named_Titles",
    }
    LOCAL_STATUS_ICONS = {
        True: "✅",
        False: "⬜",
    }
    MATCH_TYPE_ICONS = {
        "exact": "🎯",
        "normalized": "🟡",
        "normalized-region": "🟨",
        "relaxed": "🟠",
        "relaxed-region": "🟧",
        "canonical": "🔵",
        "canonical-region": "🟦",
        "fuzzy": "❓",
        "none": "❌",
    }
    SYSTEM_NAME_ALIASES = {
        "Panasonic - 3DO": "The 3DO Company - 3DO",
        "Sega - Mega-CD": "Sega - Mega-CD - Sega CD",
        "Sega - Mega Drive": "Sega - Mega Drive - Genesis",
        "Sega - Master System": "Sega - Master System - Mark III",
        "MAME 2003-Plus": "MAME",
    }
    _directory_cache = {}
    _boxart_index_cache = {}
    _asset_folder_cache = {}

    def __init__(self, default, transport):
        super().__init__(default, transport)
        self._summary_rows = []

    def setup(self, playlist):
        self.playlist = playlist
        self._cache_stats = {
            "memory_hits": 0,
            "disk_hits": 0,
            "network_fetches": 0,
        }
        item_count = 1
        src_playlists = self.default.get("src_playlists")
        if src_playlists:
            playlist_path = Path(src_playlists) / self.playlist.get("name")
            if playlist_path.is_file():
                with open(playlist_path, encoding="utf-8") as file:
                    item_count = max(1, len(json.load(file).get("items", [])))
        if self.default.get("_update_thumbnails_apply"):
            # One search step plus three asset steps per playlist item.
            self.size = item_count * 4
        else:
            self.size = item_count
        self.transfer_bytes = 0

    def _fetch_url_text(self, url):
        with urlopen(url, timeout=30) as response:
            return response.read().decode("utf-8", errors="replace")

    def _cache_dir(self):
        configured = self.default.get("_thumbnail_cache_dir")
        if configured:
            return Path(configured)
        return Path(".cache") / "retrosync" / "thumbnail-index"

    def _cache_file_for_url(self, url):
        digest = sha256(url.encode("utf-8")).hexdigest()
        return self._cache_dir() / f"{digest}.json"

    def _read_cached_directory_listing(self, url):
        if self.default.get("_no_thumbnail_cache") or self.default.get("_refresh_thumbnail_cache"):
            return None
        cache_file = self._cache_file_for_url(url)
        if not cache_file.is_file():
            return None
        try:
            data = json.loads(cache_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if data.get("version") != self.DIRECTORY_CACHE_VERSION:
            return None
        if data.get("url") != url:
            return None
        created_at = data.get("created_at")
        if not isinstance(created_at, int | float):
            return None
        if (time.time() - float(created_at)) > self.DIRECTORY_CACHE_TTL_SECONDS:
            return None
        entries = data.get("entries")
        if not isinstance(entries, list):
            return None
        return entries

    def _write_cached_directory_listing(self, url, entries):
        if self.default.get("_no_thumbnail_cache"):
            return
        cache_dir = self._cache_dir()
        try:
            cache_dir.mkdir(parents=True, exist_ok=True)
            cache_file = self._cache_file_for_url(url)
            cache_file.write_text(
                json.dumps(
                    {
                        "version": self.DIRECTORY_CACHE_VERSION,
                        "url": url,
                        "created_at": time.time(),
                        "entries": entries,
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
        except OSError:
            logger.debug("thumbnail cache write failed for url=%s", url)

    def _parse_directory_listing(self, url):
        if url in self._directory_cache:
            self._cache_stats["memory_hits"] += 1
            return self._directory_cache[url]

        cached_entries = self._read_cached_directory_listing(url)
        if cached_entries is not None:
            self._cache_stats["disk_hits"] += 1
            self._directory_cache[url] = cached_entries
            return cached_entries

        self._cache_stats["network_fetches"] += 1
        document = html.fromstring(self._fetch_url_text(url))
        entries = []
        for href in document.xpath("//a[@href]/@href"):
            if not href or href.startswith("?") or href.startswith("#"):
                continue
            absolute_url = urljoin(url, href)
            parsed_path = urlparse(absolute_url).path
            name = unquote(parsed_path.rstrip("/").split("/")[-1])
            if name in {"", "."}:
                continue
            entries.append(
                {
                    "name": name,
                    "url": absolute_url,
                    "is_dir": href.endswith("/"),
                }
            )

        self._directory_cache[url] = entries
        self._write_cached_directory_listing(url, entries)
        return entries

    def _cache_status_summary(self):
        stats = getattr(self, "_cache_stats", None) or {}
        memory_hits = stats.get("memory_hits", 0)
        disk_hits = stats.get("disk_hits", 0)
        network_fetches = stats.get("network_fetches", 0)

        if self.default.get("_no_thumbnail_cache"):
            mode = "cache: disabled"
        elif self.default.get("_refresh_thumbnail_cache"):
            mode = "cache: refresh"
        else:
            mode = "cache: normal"

        return f"{mode} | memory {memory_hits} | disk {disk_hits} | network {network_fetches}"

    def _format_report_text(self, value, max_width=None):
        text = "" if value is None else str(value)
        width = max_width or self.REPORT_TEXT_COLUMN_MIN_WIDTH
        if width <= 1 or len(text) <= width:
            return text
        return f"{text[: width - 1]}…"

    def _report_text_column_width(self, console_width):
        fixed_columns_width = 4 + 5 + 7
        borders_and_padding = 17
        available = console_width - fixed_columns_width - borders_and_padding
        width = available // 3
        return max(self.REPORT_TEXT_COLUMN_MIN_WIDTH, width)

    def _summary_coverage(self, rows):
        rom_count = len(rows)
        match_count = sum(1 for row in rows if row["match_type"] != "none")
        coverage = 0.0 if rom_count == 0 else (match_count / rom_count) * 100
        return rom_count, match_count, coverage

    def _render_summary_table(self):
        console = Console()
        table = Table(title="Thumbnail Coverage Summary")
        table.add_column("System")
        table.add_column("ROMs", justify="right")
        table.add_column("Matches", justify="right")
        table.add_column("Coverage", justify="right")
        for row in self._summary_rows:
            table.add_row(
                row["system"],
                str(row["rom_count"]),
                str(row["match_count"]),
                f"{row['coverage']:.1f}%",
            )
        with console.capture() as capture:
            console.print(table)
        return capture.get()

    def consume_final_deferred_messages(self):
        if self._summary_rows:
            self.add_final_deferred_message(self._render_summary_table())
            self._summary_rows = []
        return super().consume_final_deferred_messages()

    def _normalize_system_key(self, name):
        normalized = name.lower().replace(".lpl", "")
        normalized = re.sub(r"\bthe\b", "", normalized)
        normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
        return re.sub(r"\s+", " ", normalized).strip()

    def _resolve_remote_system_dir(self):
        root_url = self.default.get("thumbnail_url", self.LIBRETRO_THUMBNAIL_URL)
        remote_dirs = {
            entry["name"]: entry["url"]
            for entry in self._parse_directory_listing(root_url)
            if entry["is_dir"] and entry["name"] != ".."
        }
        system_name = Path(self.playlist.get("name", "")).stem

        if system_name in remote_dirs:
            return system_name, remote_dirs[system_name]

        aliased_name = self.SYSTEM_NAME_ALIASES.get(system_name)
        if aliased_name and aliased_name in remote_dirs:
            return aliased_name, remote_dirs[aliased_name]

        normalized_lookup = {self._normalize_system_key(name): name for name in remote_dirs}
        normalized_system = self._normalize_system_key(system_name)
        if normalized_system in normalized_lookup:
            matched = normalized_lookup[normalized_system]
            return matched, remote_dirs[matched]

        best_name = None
        best_score = 0.0
        for candidate in remote_dirs:
            score = Levenshtein.ratio(normalized_system, self._normalize_system_key(candidate))
            if score > best_score:
                best_score = score
                best_name = candidate
        if best_name and best_score >= 0.8:
            return best_name, remote_dirs[best_name]
        return None, None

    def build_remote_thumbnail_index(self):
        resolved_system_name, system_url = self._resolve_remote_system_dir()
        if system_url is None:
            return resolved_system_name, self.build_thumbnail_index_from_names([])

        boxart_url = urljoin(system_url.rstrip("/") + "/", "Named_Boxarts/")
        cache_key = (resolved_system_name, boxart_url)
        if cache_key in self._boxart_index_cache:
            return resolved_system_name, self._boxart_index_cache[cache_key]

        url_map = {}
        names = []
        for entry in self._parse_directory_listing(boxart_url):
            if entry["is_dir"] or entry["name"] == "..":
                continue
            if Path(entry["name"]).suffix.lower() not in {".png", ".jpg", ".jpeg"}:
                continue
            stem = Path(entry["name"]).stem
            names.append(stem)
            url_map[stem] = entry["url"]

        index = self.build_thumbnail_index_from_names(names, url_map=url_map)
        self._boxart_index_cache[cache_key] = index
        return resolved_system_name, index

    def _build_remote_asset_folder_map(self, system_url, folder_name):
        folder_url = urljoin(system_url.rstrip("/") + "/", f"{folder_name}/")
        cache_key = (system_url, folder_name)
        if cache_key in self._asset_folder_cache:
            return self._asset_folder_cache[cache_key]

        asset_map = {}
        for entry in self._parse_directory_listing(folder_url):
            if entry["is_dir"] or entry["name"] == "..":
                continue
            if Path(entry["name"]).suffix.lower() not in {".png", ".jpg", ".jpeg"}:
                continue
            asset_map[Path(entry["name"]).stem] = {
                "url": entry["url"],
                "ext": Path(entry["name"]).suffix.lower(),
            }
        self._asset_folder_cache[cache_key] = asset_map
        return asset_map

    def _load_playlist_items(self):
        playlist_path = Path(self.default.get("src_playlists")) / self.playlist.get("name")
        with open(playlist_path, encoding="utf-8") as file:
            data = json.load(file)
        return data.get("items", [])

    def _has_local_asset(self, thumbnail_name, folder_name):
        if not thumbnail_name:
            return False
        src_thumbnails = self.default.get("src_thumbnails")
        if not src_thumbnails:
            return False
        system_name = Path(self.playlist.get("name", "")).stem
        asset_dir = Path(src_thumbnails) / system_name / folder_name
        if not asset_dir.is_dir():
            return False
        for ext in [".png", ".jpg", ".jpeg"]:
            if (asset_dir / f"{thumbnail_name}{ext}").is_file():
                return True
        return False

    def _download_bytes(self, url):
        with urlopen(url, timeout=30) as response:
            return response.read()

    def _write_local_asset(self, thumbnail_name, folder_name, asset_info):
        if not asset_info or not thumbnail_name:
            return False
        src_thumbnails = self.default.get("src_thumbnails")
        if not src_thumbnails:
            return False
        system_name = Path(self.playlist.get("name", "")).stem
        asset_dir = Path(src_thumbnails) / system_name / folder_name
        asset_dir.mkdir(parents=True, exist_ok=True)
        ext = asset_info.get("ext", ".png")
        target_path = asset_dir / f"{thumbnail_name}{ext}"
        if target_path.is_file():
            return False
        if self.transport.dry_run:
            return True
        target_path.write_bytes(self._download_bytes(asset_info["url"]))
        return True

    def _load_playlist_document(self):
        playlist_path = Path(self.default.get("src_playlists")) / self.playlist.get("name")
        with open(playlist_path, encoding="utf-8") as file:
            return playlist_path, json.load(file)

    def build_report_rows(self, callback=None, cancel_check=None):
        resolved_system_name, system_url = self._resolve_remote_system_dir()
        if system_url is None:
            self.thumbnail_index = self.build_thumbnail_index_from_names([])
            boxart_assets = {}
            title_assets = {}
            snap_assets = {}
        else:
            self.thumbnail_index = self.build_remote_thumbnail_index()[1]
            boxart_assets = self._build_remote_asset_folder_map(
                system_url, self.ASSET_FOLDERS["boxart"]
            )
            title_assets = self._build_remote_asset_folder_map(
                system_url, self.ASSET_FOLDERS["title"]
            )
            snap_assets = self._build_remote_asset_folder_map(
                system_url, self.ASSET_FOLDERS["snap"]
            )
        rows = []
        for idx, item in enumerate(self._load_playlist_items()):
            if cancel_check and cancel_check():
                raise TransportError("Transfer interrupted by user.")
            rom_path = item.get("path", "")
            stem = Path(rom_path.split("#")[0]).stem
            label = item.get("label", stem)
            source_feature_map = {label: self.parse_title_features(label)}
            if stem != label:
                source_feature_map[stem] = self.parse_title_features(stem)
            match = self.match_thumbnail_candidate(
                stem,
                label,
                allow_fuzzy=True,
                source_feature_map=source_feature_map,
            )
            matched_name = match["matched"] if match else ""
            rows.append(
                {
                    "item_index": idx,
                    "system": Path(self.playlist.get("name", "")).stem,
                    "remote_system": resolved_system_name or "",
                    "rom": Path(rom_path.split("#")[0]).name,
                    "label": label,
                    "thumbnail": matched_name,
                    "match_type": match["match_type"] if match else "none",
                    "score": match["score"] if match else "",
                    "local_present": self._has_local_asset(
                        matched_name, self.ASSET_FOLDERS["boxart"]
                    )
                    if match
                    else False,
                    "asset_urls": {
                        "boxart": boxart_assets.get(matched_name),
                        "title": title_assets.get(matched_name),
                        "snap": snap_assets.get(matched_name),
                    },
                    "url": match["url"] if match else "",
                }
            )
            if callback:
                callback()
        return rows

    def _should_apply_row(self, row):
        if not row["thumbnail"]:
            return False
        return row["match_type"] != "none"

    def apply_rows(self, rows, callback=None, cancel_check=None):
        playlist_path, playlist_doc = self._load_playlist_document()
        items = playlist_doc.get("items", [])
        changed = False
        for row in rows:
            if cancel_check and cancel_check():
                raise TransportError("Transfer interrupted by user.")
            if not self._should_apply_row(row):
                if callback:
                    callback()
                    callback()
                    callback()
                continue
            matched_name = row["thumbnail"]
            for folder_key, folder_name in self.ASSET_FOLDERS.items():
                if cancel_check and cancel_check():
                    raise TransportError("Transfer interrupted by user.")
                self._write_local_asset(
                    matched_name, folder_name, row["asset_urls"].get(folder_key)
                )
                if callback:
                    callback()
            if row["item_index"] < len(items):
                item = items[row["item_index"]]
                if item.get("label") != matched_name:
                    item["label"] = matched_name
                    changed = True
            row["local_present"] = self._has_local_asset(
                matched_name, self.ASSET_FOLDERS["boxart"]
            ) or bool(row["asset_urls"].get("boxart"))

        if changed and not self.transport.dry_run:
            playlist_path.write_text(json.dumps(playlist_doc, indent=2), encoding="utf-8")

    def build_apply_preview_rows(self, rows):
        preview_rows = []
        for row in rows:
            if not self._should_apply_row(row):
                continue
            matched_name = row["thumbnail"]
            system_name = Path(self.playlist.get("name", "")).stem
            for folder_key, folder_name in self.ASSET_FOLDERS.items():
                asset_info = row["asset_urls"].get(folder_key)
                if not asset_info:
                    continue
                if self._has_local_asset(matched_name, folder_name):
                    continue
                src_thumbnails = self.default.get("src_thumbnails")
                asset_dir = (
                    Path(src_thumbnails) / system_name / folder_name if src_thumbnails else Path("")
                )
                ext = asset_info.get("ext", ".png")
                preview_rows.append(
                    {
                        "operation": "download",
                        "rom": row["rom"],
                        "label": row["label"],
                        "thumbnail": matched_name,
                        "source": asset_info["url"],
                        "destination": str(asset_dir / f"{matched_name}{ext}")
                        if src_thumbnails
                        else "",
                        "details": f"Download {folder_key} asset for matched thumbnail.",
                    }
                )
            if row["label"] != matched_name:
                playlist_path = Path(self.default.get("src_playlists")) / self.playlist.get("name")
                preview_rows.append(
                    {
                        "operation": "rewrite",
                        "rom": row["rom"],
                        "label": row["label"],
                        "thumbnail": matched_name,
                        "source": str(playlist_path),
                        "destination": str(playlist_path),
                        "details": f"Rewrite playlist label from '{row['label']}' to '{matched_name}'.",
                    }
                )
        return preview_rows

    def do(self, callback=None, cancel_check=None):
        if cancel_check and cancel_check():
            raise TransportError("Transfer interrupted by user.")

        rows = self.build_report_rows(callback=callback, cancel_check=cancel_check)
        if self.default.get("_update_thumbnails_apply"):
            self.apply_rows(rows, callback=callback, cancel_check=cancel_check)
        rom_count, match_count, coverage = self._summary_coverage(rows)
        self._summary_rows.append(
            {
                "system": Path(self.playlist.get("name", "")).stem,
                "rom_count": rom_count,
                "match_count": match_count,
                "coverage": coverage,
            }
        )
        console = Console()
        text_width = self._report_text_column_width(console.size.width)
        table = Table(title=f"Thumbnail Matches: {Path(self.playlist.get('name', '')).stem}")
        table.add_column("ROM", width=text_width, no_wrap=True)
        table.add_column("Label", width=text_width, no_wrap=True)
        table.add_column("Thumbnail", width=text_width, no_wrap=True)
        table.add_column("Have")
        table.add_column("Match")
        table.add_column("Score", justify="right")
        for row in rows:
            table.add_row(
                self._format_report_text(row["rom"], text_width),
                self._format_report_text(row["label"], text_width),
                self._format_report_text(row["thumbnail"], text_width),
                self.LOCAL_STATUS_ICONS[row["local_present"]],
                self.MATCH_TYPE_ICONS.get(row["match_type"], row["match_type"]),
                str(row["score"]),
            )
        table.add_section()
        table.add_row(
            "Cache",
            self._format_report_text(self._cache_status_summary(), text_width),
            "",
            "",
            "",
            "",
        )
        with console.capture() as capture:
            console.print(table)
        self.add_deferred_message(capture.get())


class PlaylistUpdatecJob(PlaylistUpdateJob):
    pass
