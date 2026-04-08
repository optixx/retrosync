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


def test_shader_sync_does_not_warn_when_local_source_shader_is_missing(tmp_path):
    transport = DummyTransport()
    job = ShaderSync(
        {
            "src_retroarch_base": str(tmp_path / "retroarch-src"),
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
    assert messages == ["Shader presets generated: 2"]


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
        "Warning: remote shader preset not found for PrBoom: interpolation/lanczos2.slangp",
        "Shader presets generated: 1",
    ]


def test_shader_sync_uses_first_remote_existing_shader_candidate_per_backend():
    transport = DummyTransport()
    transport.remote_files = {
        Path("/retroarch/config/shaders/shaders_glsl/interpolation/quilez.glslp"),
        Path("/retroarch/config/shaders/shaders_glsl/interpolation/bilinear.glslp"),
        Path("/retroarch/config/shaders/shaders_slang/interpolation/quilez.slangp"),
        Path("/retroarch/config/shaders/shaders_slang/interpolation/lanczos2.slangp"),
    }
    job = ShaderSync(
        {
            "dest_retroarch_base": "/retroarch/config",
            "_shaders": [
                {
                    "name": "Mupen64Plus-Next",
                    "shader": [
                        "interpolation/quilez.glslp",
                        "interpolation/bilinear.glslp",
                        "interpolation/quilez.slangp",
                        "interpolation/lanczos2.slangp",
                    ],
                },
            ],
        },
        [],
        transport,
    )

    job.do()

    assert len(transport.copies) == 2
    assert (
        transport.copies[0][0]
        == '#reference "../../shaders/shaders_glsl/interpolation/quilez.glslp"\n'
    )
    assert transport.copies[0][1] == Path(
        "/retroarch/config/config/Mupen64Plus-Next/Mupen64Plus-Next.glslp"
    )
    assert (
        transport.copies[1][0]
        == '#reference "../../shaders/shaders_slang/interpolation/quilez.slangp"\n'
    )
    assert transport.copies[1][1] == Path(
        "/retroarch/config/config/Mupen64Plus-Next/Mupen64Plus-Next.slangp"
    )
    assert job.consume_deferred_messages() == ["Shader presets generated: 2"]


def test_shader_sync_warns_when_no_remote_shader_candidate_exists():
    transport = DummyTransport()
    transport.remote_files = set()
    job = ShaderSync(
        {
            "dest_retroarch_base": "/retroarch/config",
            "_shaders": [
                {
                    "name": "Mupen64Plus-Next",
                    "shader": [
                        "interpolation/bilinear.glslp",
                        "interpolation/lanczos2.slangp",
                    ],
                },
            ],
        },
        [],
        transport,
    )

    job.do()

    assert len(transport.copies) == 0
    assert job.consume_deferred_messages() == [
        "Warning: remote shader preset not found for Mupen64Plus-Next: interpolation/bilinear.glslp, interpolation/lanczos2.slangp",
        "Shader presets generated: 0",
    ]
