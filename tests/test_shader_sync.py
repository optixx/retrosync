from pathlib import Path

from retrosync_core.jobs import ShaderSync


class DummyTransport:
    def __init__(self):
        self.copies = []
        self.remote_files = None

    def copy_file(self, src_filename, dest_filename, cancel_check=None):
        _ = cancel_check
        self.copies.append((Path(src_filename).read_text(encoding="utf-8"), Path(dest_filename)))

    def remote_file_exists(self, path):
        if self.remote_files is None:
            return None
        return Path(path) in self.remote_files


def test_shader_sync_generates_preset_files_from_config():
    transport = DummyTransport()
    job = ShaderSync(
        {
            "dest_retroarch_base": "/retroarch/config",
            "_shaders": [
                {"name": "Snes9x", "shader": "crt-guest-advanced.slangp"},
                {"name": "FinalBurn Neo", "shader": "crt/crt-guest-advanced.slangp"},
            ],
        },
        [],
        transport,
    )

    job.do()

    assert job.size == 2
    assert len(transport.copies) == 2
    assert (
        transport.copies[0][0]
        == '#reference "../../shaders/shaders_slang/crt-guest-advanced.slangp"\n'
    )
    assert (
        transport.copies[0][1] == Path("/retroarch/config") / "config" / "Snes9x" / "Snes9x.slangp"
    )
    assert (
        transport.copies[1][0]
        == '#reference "../../shaders/shaders_slang/crt/crt-guest-advanced.slangp"\n'
    )
    messages = job.consume_deferred_messages()
    assert messages == ["Shader presets generated: 2"]


def test_shader_sync_warns_when_local_source_shader_is_missing(tmp_path):
    src_base = tmp_path / "retroarch-src"
    shader_root = src_base / "shaders" / "shaders_slang" / "crt"
    shader_root.mkdir(parents=True)
    (shader_root / "crt-guest-advanced.slangp").write_text("", encoding="utf-8")

    transport = DummyTransport()
    job = ShaderSync(
        {
            "src_retroarch_base": str(src_base),
            "dest_retroarch_base": "/retroarch/config",
            "_shaders": [
                {"name": "Snes9x", "shader": "crt/crt-guest-advanced.slangp"},
                {"name": "PPSSPP", "shader": "interpolation/sharp-bilinear-2x-prescale.slangp"},
            ],
        },
        [],
        transport,
    )

    job.do()

    assert len(transport.copies) == 2
    messages = job.consume_deferred_messages()
    assert messages == [
        "Warning: local shader preset not found: interpolation/sharp-bilinear-2x-prescale.slangp",
        "Shader presets generated: 2",
    ]


def test_shader_sync_skips_generating_preset_when_remote_shader_is_missing():
    transport = DummyTransport()
    transport.remote_files = {
        Path("/retroarch/config/shaders/shaders_slang/crt/crt-guest-advanced.slangp")
    }
    job = ShaderSync(
        {
            "dest_retroarch_base": "/retroarch/config",
            "_shaders": [
                {"name": "Snes9x", "shader": "crt/crt-guest-advanced.slangp"},
                {"name": "PrBoom", "shader": "interpolation/lanczos2.slangp"},
            ],
        },
        [],
        transport,
    )

    job.do()

    assert len(transport.copies) == 1
    assert transport.copies[0][1] == Path("/retroarch/config/config/Snes9x/Snes9x.slangp")
    messages = job.consume_deferred_messages()
    assert messages == [
        "Warning: remote shader preset not found: interpolation/lanczos2.slangp",
        "Shader presets generated: 1",
    ]
