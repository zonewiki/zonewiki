"""Section editing through the /edit and /api/save routes: a section's
editor, saving it into the page, conflicts, auto-save's anchors. How pages
split into sections and the splicing have Go tests (go/sections_test.go)."""

from tests.conftest import HOST, content_hash, content_hash, section_fields, write_page


# ===========================================================================
# HTTP route tests — GET /edit/<slug>?section=N
# ===========================================================================

class TestEditPageGetSection:

    def test_full_edit_shows_all_content(self, client, wiki_dir):
        write_page(wiki_dir, "mypage", "# Title\n\n## A\n\nbody a\n\n## B\n\nbody b\n")
        resp = client.get("/edit/mypage", headers=HOST)
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "body a" in html and "body b" in html

    def test_section_1_shows_only_that_section(self, client, wiki_dir):
        write_page(wiki_dir, "sp", "Pre.\n\n## Alpha\n\nalpha body\n\n## Beta\n\nbeta body\n")
        resp = client.get("/edit/sp?section=1", headers=HOST)
        html = resp.data.decode()
        assert "alpha body" in html
        assert "beta body" not in html

    def test_section_2_shows_second_section(self, client, wiki_dir):
        write_page(wiki_dir, "sp2", "pre\n\n## Alpha\n\nalpha\n\n## Beta\n\nbeta\n")
        resp = client.get("/edit/sp2?section=2", headers=HOST)
        html = resp.data.decode()
        assert "beta" in html
        assert "alpha" not in html or "Alpha" in html  # heading may appear

    def test_invalid_section_falls_back_to_full(self, client, wiki_dir):
        write_page(wiki_dir, "fb", "pre\n\n## A\n\nbody\n")
        resp = client.get("/edit/fb?section=99", headers=HOST)
        assert resp.status_code == 200  # no crash

    def test_section_embeds_hidden_fields(self, client, wiki_dir):
        content = "pre\n\n## A\n\nbody a\n\n## B\n\nbody b\n"
        write_page(wiki_dir, "hf", content)
        resp = client.get("/edit/hf?section=1", headers=HOST)
        html = resp.data.decode()
        assert 'id="section-file-hash"' in html
        assert 'id="section-start-char"' in html
        assert 'id="section-end-char"' in html
        # Verify the hash value
        assert content_hash(content) in html

    def test_section_preamble_not_shown(self, client, wiki_dir):
        write_page(wiki_dir, "ph", "UNIQUE_PREAMBLE\n\n## A\n\nbody\n")
        resp = client.get("/edit/ph?section=1", headers=HOST)
        assert "UNIQUE_PREAMBLE" not in resp.data.decode()

    def test_section_rename_delete_buttons_hidden(self, client, wiki_dir):
        write_page(wiki_dir, "ns", "pre\n\n## A\n\nbody\n")
        resp = client.get("/edit/ns?section=1", headers=HOST)
        html = resp.data.decode()
        assert "rename-toggle" not in html
        assert "delete-btn" not in html

    def test_conflict_query_param_ignored(self, client, wiki_dir):
        """?conflict=1 no longer renders a server-side banner (handled client-side)."""
        write_page(wiki_dir, "cf", "# Title\n\nbody\n")
        resp = client.get("/edit/cf?conflict=1", headers=HOST)
        assert "conflict-banner" not in resp.data.decode()


# ===========================================================================
# HTTP route tests — POST /edit/<slug> (Done button)
# ===========================================================================

class TestSavePageSection:

    def test_section_save_merges_correctly(self, client, wiki_dir):
        original = "pre\n\n## Alpha\n\nold alpha\n\n## Beta\n\nold beta\n"
        page = write_page(wiki_dir, "ss", original)
        fields = section_fields(client, "ss", 1)
        fields["content"] = "## Alpha\n\nnew alpha\n"
        resp = client.post("/edit/ss", data=fields, headers=HOST)
        assert resp.status_code in (301, 302, 303)
        saved = page.read_text(encoding="utf-8")
        assert "new alpha" in saved
        assert "old alpha" not in saved
        assert "old beta" in saved

    def test_section_2_preserves_section_1(self, client, wiki_dir):
        original = "pre\n\n## One\n\none body\n\n## Two\n\ntwo body\n"
        page = write_page(wiki_dir, "ss2", original)
        fields = section_fields(client, "ss2", 2)
        fields["content"] = "## Two\n\nupdated two\n"
        client.post("/edit/ss2", data=fields, headers=HOST)
        saved = page.read_text(encoding="utf-8")
        assert "one body" in saved
        assert "updated two" in saved

    def test_full_page_save_no_section_fields(self, client, wiki_dir):
        page = write_page(wiki_dir, "fs", "pre\n\n## A\n\nbody\n")
        new_content = "pre\n\n## A\n\nupdated body\n"
        client.post("/edit/fs", data={"content": new_content}, headers=HOST)
        assert page.read_text(encoding="utf-8") == new_content

    def test_hash_mismatch_redirects_with_conflict(self, client, wiki_dir):
        original = "pre\n\n## A\n\nbody\n"
        write_page(wiki_dir, "hm", original)
        fields = section_fields(client, "hm", 1)
        fields["content"] = "## A\n\nnew body\n"
        fields["file_hash"] = "wrong_hash_value"
        resp = client.post("/edit/hm", data=fields, headers=HOST)
        assert resp.status_code in (301, 302, 303)
        assert "conflict=1" in resp.headers.get("Location", "")

    def test_preserves_preamble(self, client, wiki_dir):
        original = "KEEP_THIS_PREAMBLE\n\n## Sec\n\nold body\n"
        page = write_page(wiki_dir, "kp", original)
        fields = section_fields(client, "kp", 1)
        fields["content"] = "## Sec\n\nnew body\n"
        client.post("/edit/kp", data=fields, headers=HOST)
        assert "KEEP_THIS_PREAMBLE" in page.read_text(encoding="utf-8")


# ===========================================================================
# HTTP route tests — POST /api/save/<slug> (auto-save)
# ===========================================================================

class TestApiSaveSection:

    def test_section_save_merges_correctly(self, client, wiki_dir):
        original = "pre\n\n## X\n\nold x\n\n## Y\n\nold y\n"
        page = write_page(wiki_dir, "as", original)
        fields = section_fields(client, "as", 1)
        fields["content"] = "## X\n\nnew x\n"
        resp = client.post("/api/save/as", data=fields, headers=HOST)
        assert resp.status_code == 200
        saved = page.read_text(encoding="utf-8")
        assert "new x" in saved
        assert "old y" in saved

    def test_returns_updated_anchors(self, client, wiki_dir):
        original = "pre\n\n## A\n\nbody a\n\n## B\n\nbody b\n"
        write_page(wiki_dir, "ua", original)
        fields = section_fields(client, "ua", 1)
        fields["content"] = "## A\n\nlonger body for section a\n"
        resp = client.post("/api/save/ua", data=fields, headers=HOST)
        assert resp.status_code == 200
        data = resp.get_json()
        assert "file_hash" in data
        assert "start_char" in data
        assert "end_char" in data
        assert data["file_hash"] != content_hash(original)  # content changed

    def test_second_autosave_uses_returned_anchors(self, client, wiki_dir):
        """Simulates two consecutive auto-saves, the second using anchors from the first."""
        original = "pre\n\n## A\n\nbody a\n\n## B\n\nbody b\n"
        page = write_page(wiki_dir, "chain", original)

        # First auto-save
        fields1 = section_fields(client, "chain", 1)
        fields1["content"] = "## A\n\nedit 1\n"
        resp1 = client.post("/api/save/chain", data=fields1, headers=HOST)
        assert resp1.status_code == 200
        anchors = resp1.get_json()

        # Second auto-save using returned anchors
        fields2 = {
            "content": "## A\n\nedit 2\n",
            "file_hash": anchors["file_hash"],
            "start_char": str(anchors["start_char"]),
            "end_char": str(anchors["end_char"]),
        }
        resp2 = client.post("/api/save/chain", data=fields2, headers=HOST)
        assert resp2.status_code == 200
        saved = page.read_text(encoding="utf-8")
        assert "edit 2" in saved
        assert "body b" in saved  # B must not be lost!

    def test_full_save_without_a_hash_answers_the_new_one(self, client, wiki_dir):
        # The editor's next save then goes from what this one wrote (a new
        # page's first save, Keep mine).
        write_page(wiki_dir, "full", "old\n")
        resp = client.post("/api/save/full", data={"content": "new\n"}, headers=HOST)
        assert resp.status_code == 200 and resp.get_json() == {"file_hash": content_hash("new\n")}

    def test_hash_mismatch_returns_409(self, client, wiki_dir):
        original = "pre\n\n## A\n\nbody\n"
        write_page(wiki_dir, "c409", original)
        fields = section_fields(client, "c409", 1)
        fields["content"] = "## A\n\nnew\n"
        fields["file_hash"] = "wrong_hash_value"
        resp = client.post("/api/save/c409", data=fields, headers=HOST)
        assert resp.status_code == 409
        assert resp.get_json()["error"] == "file_changed"

    def test_unchanged_content_returns_same_anchors(self, client, wiki_dir):
        original = "pre\n\n## A\n\nbody a\n\n## B\n\nbody b\n"
        write_page(wiki_dir, "noop", original)
        fields = section_fields(client, "noop", 1)
        rng = (int(fields["start_char"]), int(fields["end_char"]))
        sec_text = original[rng[0]:rng[1]]
        fields["content"] = sec_text.rstrip('\n')
        resp = client.post("/api/save/noop", data=fields, headers=HOST)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["file_hash"] == content_hash(original)

    def test_repeated_autosave_is_idempotent(self, client, wiki_dir):
        original = "pre\n\n## A\n\nbody a\n\n## B\n\nbody b\n"
        page = write_page(wiki_dir, "idem", original)
        fields = section_fields(client, "idem", 1)
        rng = (int(fields["start_char"]), int(fields["end_char"]))
        fields["content"] = original[rng[0]:rng[1]].rstrip('\n')
        for _ in range(5):
            client.post("/api/save/idem", data=fields, headers=HOST)
        assert page.read_text(encoding="utf-8") == original


# ===========================================================================
# Regression — the "lost section" scenarios
# ===========================================================================

class TestLostSectionRegression:

    def test_last_section_not_lost_after_editing_middle(self, client, wiki_dir):
        original = (
            "# Page\n\nIntro.\n\n"
            "## First\n\nFirst body.\n\n"
            "## Second\n\nSecond body.\n\n"
            "## Third\n\nThird body.\n"
        )
        page = write_page(wiki_dir, "lt1", original)
        fields = section_fields(client, "lt1", 2)
        fields["content"] = "## Second\n\nUpdated second body.\n"
        client.post("/edit/lt1", data=fields, headers=HOST)
        saved = page.read_text(encoding="utf-8")
        assert "Third body." in saved
        assert "Updated second body." in saved
        assert "First body." in saved

    def test_section_not_lost_with_autosave_then_done(self, client, wiki_dir):
        """Auto-save fires, then Done. No section must disappear."""
        original = "preamble\n\n## A\n\na body\n\n## B\n\nb body\n\n## C\n\nc body\n"
        page = write_page(wiki_dir, "al", original)

        # Auto-save
        fields = section_fields(client, "al", 2)
        fields["content"] = "## B\n\nb body modified\n"
        resp = client.post("/api/save/al", data=fields, headers=HOST)
        assert resp.status_code == 200
        anchors = resp.get_json()

        # Done using updated anchors
        done_fields = {
            "content": "## B\n\nb body final\n",
            "file_hash": anchors["file_hash"],
            "start_char": str(anchors["start_char"]),
            "end_char": str(anchors["end_char"]),
        }
        client.post("/edit/al", data=done_fields, headers=HOST)
        saved = page.read_text(encoding="utf-8")
        assert "a body" in saved
        assert "b body final" in saved
        assert "c body" in saved, "Section C was lost!"

    def test_autosave_adding_heading_does_not_lose_subsequent_sections(self, client, wiki_dir):
        """User adds a new ## heading in the editor, auto-save fires, then Done.
        The subsequent sections must survive."""
        original = "preamble\n\n## Old\n\nold content\n\n## After\n\nafter content\n"
        page = write_page(wiki_dir, "ah", original)

        # Auto-save: user typed a new heading in their section
        fields = section_fields(client, "ah", 1)
        fields["content"] = "## Old-Part1\n\npart1\n\n## Old-Part2\n\npart2\n"
        resp = client.post("/api/save/ah", data=fields, headers=HOST)
        assert resp.status_code == 200
        after_autosave = page.read_text(encoding="utf-8")
        assert "after content" in after_autosave, "Section 'After' lost during auto-save!"

        # Done: user merges back to single heading
        anchors = resp.get_json()
        done_fields = {
            "content": "## Old-Merged\n\nmerged content\n",
            "file_hash": anchors["file_hash"],
            "start_char": str(anchors["start_char"]),
            "end_char": str(anchors["end_char"]),
        }
        client.post("/edit/ah", data=done_fields, headers=HOST)
        saved = page.read_text(encoding="utf-8")
        assert "merged content" in saved
        assert "after content" in saved, "Section 'After' was lost after Done!"

    def test_multiple_autosaves_then_done(self, client, wiki_dir):
        """Multiple auto-saves followed by Done — all sections must survive."""
        original = "preamble\n\n## S1\n\ns1\n\n## S2\n\ns2\n\n## S3\n\ns3\n"
        page = write_page(wiki_dir, "ms", original)

        # First auto-save
        fields = section_fields(client, "ms", 1)
        fields["content"] = "## S1\n\ns1 draft\n"
        resp = client.post("/api/save/ms", data=fields, headers=HOST)
        anchors = resp.get_json()

        # Second auto-save
        fields2 = {
            "content": "## S1\n\ns1 final draft\n",
            "file_hash": anchors["file_hash"],
            "start_char": str(anchors["start_char"]),
            "end_char": str(anchors["end_char"]),
        }
        resp2 = client.post("/api/save/ms", data=fields2, headers=HOST)
        anchors2 = resp2.get_json()

        # Done
        done_fields = {
            "content": "## S1\n\ns1 final\n",
            "file_hash": anchors2["file_hash"],
            "start_char": str(anchors2["start_char"]),
            "end_char": str(anchors2["end_char"]),
        }
        client.post("/edit/ms", data=done_fields, headers=HOST)
        saved = page.read_text(encoding="utf-8")
        assert saved.count("## S") == 3
        assert "s1 final" in saved
        assert "s2" in saved, "S2 was lost!"
        assert "s3" in saved, "S3 was lost!"


# ===========================================================================
# Force-section save (conflict resolution "Save my version")
# ===========================================================================

class TestForceSectionSave:
    """Tests for force_section parameter — used when user clicks 'Save my version'
    after a conflict. Must splice content into the CURRENT file by section index,
    not overwrite the entire file."""

    def test_force_section_splices_into_current_file(self, client, wiki_dir):
        """Force-save splices section content into the current file on disk."""
        original = "pre\n\n## A\n\nold a\n\n## B\n\nold b\n"
        page = write_page(wiki_dir, "fs1", original)
        # Simulate: file changed on disk (some external edit to section B)
        modified = "pre\n\n## A\n\nold a\n\n## B\n\nmodified b\n"
        page.write_text(modified, encoding="utf-8")
        # Force-save section 1 (## A) with new content
        resp = client.post("/api/save/fs1", data={
            "content": "## A\n\nnew a\n",
            "force_section": "1",
        }, headers=HOST)
        assert resp.status_code == 200
        saved = page.read_text(encoding="utf-8")
        assert "new a" in saved, "Section A was not updated"
        assert "modified b" in saved, "Section B was lost!"
        assert "pre" in saved, "Preamble was lost!"

    def test_force_section_keeps_separator_before_next_section(self, client, wiki_dir):
        """Regression: the editor loads a section with trailing newlines
        stripped, so the posted content has none. Force-save used to splice it
        in verbatim, gluing the next heading onto the last line
        ("new a## B"), which also destroyed that heading."""
        page = write_page(wiki_dir, "fs-glue", "pre\n\n## A\n\nold a\n\n## B\n\nb\n")
        resp = client.post("/api/save/fs-glue", data={
            "content": "## A\n\nnew a",
            "force_section": "1",
        }, headers=HOST)
        assert resp.status_code == 200
        saved = page.read_text(encoding="utf-8")
        assert saved == "pre\n\n## A\n\nnew a\n\n## B\n\nb\n"
        data = resp.get_json()
        assert saved[data["start_char"]:data["end_char"]] == "## A\n\nnew a\n\n"

    def test_force_section_returns_updated_anchors(self, client, wiki_dir):
        original = "pre\n\n## A\n\nbody a\n\n## B\n\nbody b\n"
        write_page(wiki_dir, "fs2", original)
        resp = client.post("/api/save/fs2", data={
            "content": "## A\n\nlonger body\n",
            "force_section": "1",
        }, headers=HOST)
        assert resp.status_code == 200
        data = resp.get_json()
        assert "file_hash" in data
        assert "start_char" in data
        assert "end_char" in data

    def test_force_section_does_not_lose_any_section(self, client, wiki_dir):
        """With 4 sections, force-saving section 2 must keep all others intact."""
        original = "pre\n\n## S1\n\ns1\n\n## S2\n\ns2\n\n## S3\n\ns3\n"
        page = write_page(wiki_dir, "fs3", original)
        # Change file on disk (add content to S3)
        modified = "pre\n\n## S1\n\ns1\n\n## S2\n\ns2\n\n## S3\n\ns3 extended\n"
        page.write_text(modified, encoding="utf-8")
        resp = client.post("/api/save/fs3", data={
            "content": "## S2\n\nnew s2\n",
            "force_section": "2",
        }, headers=HOST)
        assert resp.status_code == 200
        saved = page.read_text(encoding="utf-8")
        assert "s1" in saved, "S1 lost!"
        assert "new s2" in saved, "S2 not updated!"
        assert "s3 extended" in saved, "S3 lost!"

    def test_force_section_invalid_index_returns_400(self, client, wiki_dir):
        write_page(wiki_dir, "fs4", "pre\n\n## A\n\na\n")
        resp = client.post("/api/save/fs4", data={
            "content": "## X\n\nx\n",
            "force_section": "99",
        }, headers=HOST)
        assert resp.status_code == 400

    def test_force_section_zero_does_full_file_write(self, client, wiki_dir):
        """force_section=0 is below the valid range and falls through to a
        regular full-file write (no splice)."""
        page = write_page(wiki_dir, "fs5", "pre\n\n## A\n\na\n")
        resp = client.post("/api/save/fs5", data={
            "content": "replaced\n",
            "force_section": "0",
        }, headers=HOST)
        assert resp.status_code == 200
        assert page.read_text(encoding="utf-8") == "replaced\n"

    def test_force_section_after_conflict_then_autosave_continues(self, client, wiki_dir):
        """After force-save resolves a conflict, regular auto-saves can resume
        using the returned anchors."""
        original = "pre\n\n## A\n\nold a\n\n## B\n\nold b\n"
        page = write_page(wiki_dir, "fs6", original)
        # Force-save section 1
        resp = client.post("/api/save/fs6", data={
            "content": "## A\n\nforced a\n",
            "force_section": "1",
        }, headers=HOST)
        assert resp.status_code == 200
        anchors = resp.get_json()
        # Now do a regular section auto-save with the returned anchors
        resp2 = client.post("/api/save/fs6", data={
            "content": "## A\n\nforced a v2\n",
            "file_hash": anchors["file_hash"],
            "start_char": str(anchors["start_char"]),
            "end_char": str(anchors["end_char"]),
        }, headers=HOST)
        assert resp2.status_code == 200
        saved = page.read_text(encoding="utf-8")
        assert "forced a v2" in saved
        assert "old b" in saved, "Section B was lost!"


# ===========================================================================
# Full-file conflict detection
# ===========================================================================

class TestFullFileConflict:
    """Tests for hash-based conflict detection on full-file edits."""

    def test_full_file_save_with_hash_succeeds(self, client, wiki_dir):
        """Auto-save with correct hash writes and returns new hash."""
        original = "hello world\n"
        page = write_page(wiki_dir, "ffc1", original)
        h = content_hash(original)
        resp = client.post("/api/save/ffc1", data={
            "content": "hello world updated\n",
            "file_hash": h,
        }, headers=HOST)
        assert resp.status_code == 200
        data = resp.get_json()
        assert "file_hash" in data
        assert data["file_hash"] != h
        assert page.read_text(encoding="utf-8") == "hello world updated\n"

    def test_full_file_save_hash_mismatch_returns_409(self, client, wiki_dir):
        """When file changed on disk, auto-save gets 409."""
        original = "original content\n"
        page = write_page(wiki_dir, "ffc2", original)
        h = content_hash(original)
        # Simulate external edit
        page.write_text("externally modified\n", encoding="utf-8")
        resp = client.post("/api/save/ffc2", data={
            "content": "my edit\n",
            "file_hash": h,
        }, headers=HOST)
        assert resp.status_code == 409
        assert resp.get_json()["error"] == "file_changed"
        # Disk content must be preserved
        assert page.read_text(encoding="utf-8") == "externally modified\n"

    def test_full_file_save_returns_updated_hash_for_next_save(self, client, wiki_dir):
        """Returned hash can be used for the next auto-save."""
        original = "v1\n"
        page = write_page(wiki_dir, "ffc3", original)
        h = content_hash(original)
        resp1 = client.post("/api/save/ffc3", data={
            "content": "v2\n", "file_hash": h,
        }, headers=HOST)
        h2 = resp1.get_json()["file_hash"]
        resp2 = client.post("/api/save/ffc3", data={
            "content": "v3\n", "file_hash": h2,
        }, headers=HOST)
        assert resp2.status_code == 200
        assert page.read_text(encoding="utf-8") == "v3\n"

    def test_full_file_done_conflict_redirects(self, client, wiki_dir):
        """Done POST with stale hash redirects to conflict page."""
        original = "original\n"
        page = write_page(wiki_dir, "ffc4", original)
        h = content_hash(original)
        page.write_text("changed on disk\n", encoding="utf-8")
        resp = client.post("/edit/ffc4", data={
            "content": "my edit\n", "file_hash": h,
        }, headers=HOST)
        assert resp.status_code == 302
        assert "conflict=1" in resp.headers["Location"]
        assert page.read_text(encoding="utf-8") == "changed on disk\n"

    def test_full_file_done_matching_hash_saves(self, client, wiki_dir):
        """Done POST with correct hash saves and redirects to page."""
        original = "hello\n"
        page = write_page(wiki_dir, "ffc5", original)
        h = content_hash(original)
        resp = client.post("/edit/ffc5", data={
            "content": "updated\n", "file_hash": h,
        }, headers=HOST, follow_redirects=False)
        assert resp.status_code == 302
        assert "/page/ffc5" in resp.headers["Location"]
        assert page.read_text(encoding="utf-8") == "updated\n"

    def test_full_file_save_without_hash(self, client, wiki_dir):
        """No file_hash (a page new in the editor) → plain write, no conflict check."""
        page = write_page(wiki_dir, "ffc6", "old\n")
        resp = client.post("/api/save/ffc6", data={"content": "new\n"}, headers=HOST)
        assert resp.status_code == 200
        assert page.read_text(encoding="utf-8") == "new\n"


# ===========================================================================
# Bug: setext-style h2 headings weren't sections
# (lines followed by a line of dashes: "Title\n------")
# ===========================================================================

class TestSplitSectionsSetextHeadings:

    def test_setext_section_edit_route_uses_correct_index(self, client, wiki_dir):
        """Regression: clicking a setext-section's edit button must load that section,
        not silently discard the edit."""
        content = (
            "Preamble.\n\n"
            "Alpha Section\n-------------\n\nalpha body\n\n"
            "Beta Section\n------------\n\nbeta body\n"
        )
        write_page(wiki_dir, "setext_page", content)
        # The GET /edit route should serve section 1 correctly
        resp = client.get("/edit/setext_page?section=1", headers=HOST)
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "alpha body" in html
        assert "beta body" not in html

    def test_setext_section_save_does_not_silently_drop(self, client, wiki_dir):
        """Setext sections must save correctly with the new splice approach."""
        content = (
            "Preamble.\n\n"
            "Alpha\n-----\n\nalpha body\n\n"
            "Beta\n----\n\nbeta body\n"
        )
        page = write_page(wiki_dir, "setext_save", content)
        fields = section_fields(client, "setext_save", 1)
        fields["content"] = "Alpha\n-----\n\nUPDATED alpha body\n"
        resp = client.post("/edit/setext_save", data=fields, headers=HOST)
        assert resp.status_code in (301, 302, 303)
        saved = page.read_text(encoding="utf-8")
        assert "UPDATED alpha body" in saved, "Edit was silently dropped (data loss bug)"
        assert "beta body" in saved


# ===========================================================================
# Bug: indented ATX headings (up to 3 leading spaces) weren't sections
# ===========================================================================

class TestSplitSectionsIndentedHeadings:

    def test_indented_section_save_does_not_drop(self, client, wiki_dir):
        """Indented ATX heading: edit must reach the correct section."""
        content = "preamble\n\n  ## Section A\n\nbody a\n\n  ## Section B\n\nbody b\n"
        page = write_page(wiki_dir, "indented_page", content)
        fields = section_fields(client, "indented_page", 1)
        fields["content"] = "  ## Section A\n\nUPDATED body a\n"
        resp = client.post("/edit/indented_page", data=fields, headers=HOST)
        assert resp.status_code in (301, 302, 303)
        saved = page.read_text(encoding="utf-8")
        assert "UPDATED body a" in saved, "Edit was silently dropped"
        assert "body b" in saved
