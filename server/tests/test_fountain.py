from pathlib import Path

from throughline import fountain

SAMPLES = Path(__file__).parents[2] / "samples" / "neon_courier"


def test_parse_splits_scenes_with_line_numbers():
    script = fountain.parse("Title: Pilot\n\nINT. ROOM - DAY\n\nMIRA\nHello.\n\nEXT. ROOF - NIGHT\n\nRain.\n")
    assert script.title == "Pilot"
    assert [s.heading for s in script.scenes] == ["INT. ROOM - DAY", "EXT. ROOF - NIGHT"]
    assert (script.scenes[0].line_start, script.scenes[0].line_end) == (3, 7)
    assert script.scenes[1].lines == [(10, "Rain.")]


def test_find_line_prefers_the_given_scene_and_ignores_whitespace():
    script = fountain.parse("INT. A - DAY\nthe   locket opens\nINT. B - DAY\nthe locket opens\n")
    assert fountain.find_line(script, "The locket opens", script.scenes[1]) == 4
    assert fountain.find_line(script, "the locket opens") == 2
    assert fountain.find_line(script, "not in the script") is None


def test_sample_scripts_match_their_extractions():
    from throughline.models import EpisodeExtract

    for path in sorted((SAMPLES / "scripts").glob("ep*.fountain")):
        script = fountain.parse_file(path)
        extracted = EpisodeExtract.model_validate_json((SAMPLES / "extracted" / f"{path.stem}.json").read_text())
        assert [s.heading for s in extracted.scenes] == [s.heading for s in script.scenes]
        for scene, parsed in zip(extracted.scenes, script.scenes):
            for item in [*scene.shots, *scene.events]:
                assert fountain.find_line(script, item.quote, parsed), f"{path.name}: quote not found: {item.quote!r}"
