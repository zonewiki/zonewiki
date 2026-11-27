package main

import (
	"regexp"
	"strings"

	"zonewiki/internal/mdtext"
)

// zonewikiIDList: the ids of Zonewiki's own elements that a heading's id
// could be (lower case: letters, digits, "-", "_"), as its templates,
// scripts and styles name them, and the globals its scripts read (an
// element's id is a window.x too: a heading "Mermaid" would be
// window.mermaid). An id ending in "-<number>" is its first part's too: a
// list embedded twice has "tasks-app-2", a name picker "pick-menu-3-1",
// a diagram "mermaid-4" (Mermaid's own ids: dmermaid-4, imermaid-4).
// TestZonewikisIDsAreNoHeadingsIDs finds them in the source and fails for
// one missing here.
const zonewikiIDList = `
all-groups append-heading append-page assistant-address-label
assistant-token bulk-assign-cancel bulk-assign-find bulk-list-back
bulk-list-cancel bulk-list-create bulk-list-name bulk-list-private
bulk-tag-cancel bulk-tag-input bulk-tag-ok commit-all-btn conflict-merge
conflict-overwrite conflict-reload connection-change-dialog
connection-change-label delete-btn delete-form dmermaid editor-form
editor-more editor-more-btn editor-preview editor-textarea
fmt-attach-input format-bar group-people hljs imermaid img-lightbox
import-file import-folder import-for katex left-draft
left-draft-conflict left-draft-content list-delete-btn
list-delete-cancel list-delete-close list-delete-dialog
list-delete-share list-delete-submit list-delete-to list-for-btn
list-for-cancel list-for-dialog list-for-input list-for-submit
list-new-btn list-new-cancel list-new-dialog list-new-for list-new-input
list-new-submit list-pin-btn list-rename-btn list-rename-cancel
list-rename-dialog list-rename-input list-rename-submit list-share-btn
list-share-cancel list-share-dialog list-share-for list-share-submit
list-unpin-btn merge-note mermaid mobile-preview-btn modal
modal-content-matches modal-filter modal-page-list modal-preview
modal-sort modal-unlinked modal-where new new-note-cancel new-note-form
new-note-title notes-filter notes-filter-count of-groups others
page-append-cancel page-append-dialog page-delete-cancel
page-delete-dialog page-for page-menu page-name page-privacy-cancel
page-privacy-dialog page-promote-cancel page-promote-dialog
page-rename-cancel page-rename-dialog page-rename-input pick-menu
present-btn promote-copied promote-folder promote-folders
promote-keep-date promote-link-from promote-name promote-slug rename-bar
rename-cancel rename-input rename-toggle same-name-banner same-name-new
save-dot search-modal search-mode section-end-char section-file-hash
section-heading section-idx section-start-char shared-new-banner
shared-new-keep shared-new-there shortcuts-modal shortcuts-title
tag-rename-btn tag-rename-cancel tag-rename-dialog tag-rename-input
tag-rename-submit task-add-btn task-closed-link task-cut
task-deadline-cancel task-deadline-confirm task-deadline-modal
task-deadline-text task-edit-assignee task-edit-assignee-field
task-edit-attach task-edit-batch task-edit-batch-label
task-edit-batch-list task-edit-cancel task-edit-comment
task-edit-comment-add task-edit-comment-box task-edit-comment-cancel
task-edit-comment-list task-edit-comment-open task-edit-comments
task-edit-comments-count task-edit-date-warn task-edit-date-warn-fix
task-edit-date-warn-text task-edit-desc task-edit-due task-edit-follow
task-edit-follow-btn task-edit-followers task-edit-form task-edit-format
task-edit-history task-edit-history-count task-edit-history-list
task-edit-id task-edit-importance task-edit-keep task-edit-lead
task-edit-list task-edit-list-note task-edit-modal task-edit-more
task-edit-more-keep task-edit-more-lead task-edit-more-repeat
task-edit-notes-edit-btn task-edit-preview task-edit-preview-btn
task-edit-props task-edit-quick task-edit-rec-from
task-edit-rec-interval task-edit-rec-note task-edit-rec-type
task-edit-rec-unit task-edit-start task-edit-tags task-edit-target
task-edit-title task-edit-wontdo task-filter-search task-import-cancel
task-import-error task-import-file task-import-form task-import-modal
task-import-tag task-import-title task-more-btn task-new-list-cancel
task-new-list-create task-new-list-name task-new-list-private
task-selbar task-settings task-settings-assignee task-settings-cancel
task-settings-error task-settings-form task-settings-group
task-settings-home task-settings-keep task-settings-label-focus
task-settings-label-open task-settings-limit task-settings-list
task-settings-lists task-settings-modal task-settings-more
task-settings-new task-settings-noshare task-settings-save
task-settings-tag task-settings-title task-settings-view
task-settings-where task-share-cancel task-share-confirm
task-share-modal task-share-text task-sort task-sort-reset
task-subtasks-cancel task-subtasks-confirm task-subtasks-modal
task-subtasks-text tasks-app tasks-list tasks-no-list
tasks-show-more-btn tasks-tabs timezones tldr-toggle uncommitted-badge
uncommitted-count updates-mentions-h updates-read-btn updates-tasks-h
updates-watched-h
upload-conflict-cancel upload-conflict-confirm upload-conflict-modal
upload-conflict-msg upload-conflict-name url-name-cancel
url-name-confirm url-name-href url-name-input url-name-modal user-groups
user-remove-cancel user-remove-dialog watch-read-btn wiki-pages
`

var (
	zonewikiIDSet = func() map[string]bool {
		set := map[string]bool{}
		for _, id := range strings.Fields(zonewikiIDList) {
			set[id] = true
		}
		return set
	}()
	numberedRE = regexp.MustCompile(`(?:-[0-9]+)+$`)
)

// zonewikiID: id is one of Zonewiki's own (zonewikiIDList).
func zonewikiID(id string) bool {
	return zonewikiIDSet[id] || zonewikiIDSet[numberedRE.ReplaceAllString(id, "")]
}

// headingID: the id a heading gets on a page for the id its text makes
// (mdtext.HeadingID): that id, or with "user-content-" in front where it
// is one of Zonewiki's own (zonewikiID). A heading "Modal" ahead of the
// search modal (id "modal") would stand in for it: a script finding it by
// id gets the heading, a style for #modal hides it. So no heading's id is
// Zonewiki's, as no id in a page's raw HTML is (defuseHTML); a #modal link
// written by hand still finds it (hashTarget in js/util.js).
func headingID(slug string) string {
	if zonewikiID(slug) {
		return "user-content-" + slug
	}
	return slug
}

// sectionAnchor: the id of the heading a [[Page#Section]] link points at,
// as the page renders it.
func sectionAnchor(section string) string { return headingID(mdtext.SectionAnchor(section)) }
