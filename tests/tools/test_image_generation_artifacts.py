import json


def test_handle_image_generate_returns_local_plugin_artifact(monkeypatch, tmp_path):
    from tools import image_generation_tool

    image_path = tmp_path / "plugin.png"
    image_path.write_bytes(b"png")
    monkeypatch.setattr(
        image_generation_tool,
        "_dispatch_to_plugin_provider",
        lambda prompt, aspect_ratio, **kw: json.dumps({"success": True, "image": str(image_path)}),
    )
    result = json.loads(image_generation_tool._handle_image_generate(
        {"prompt": "draw", "aspect_ratio": "square"}, task_id="plugin-task"))
    assert result == {"success": True, "image": str(image_path)}
    assert image_path.read_bytes() == b"png"
