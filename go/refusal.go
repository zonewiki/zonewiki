// Refusals: an operation that won't do what was asked says so with a
// *refusalError, why (its kind) and a message for the user. Page operations
// (pageops.go), task writes (tasks.go) and an embed's settings all refuse
// this way, and each door turns a refusal into its own answer: the page
// routes a status or a dialog's message (pageFailed), the tasks API a
// status with {"error": …} (tasksRoute), the MCP tools a tool error the
// model can correct (callMCPTool). Anything else an operation returns is a
// failure: a 500, or a protocol error.
package main

import (
	"errors"
	"net/http"
)

// refusal: why an operation refused.
type refusal int

const (
	refusedInvalid   refusal = iota // can't be done as asked: 400, or the dialog's message
	refusedNotFound                 // no such page or task, or not one the user may see: 404
	refusedExists                   // a page by the new name is there already
	refusedProtected                // the home page: never deleted or renamed
	refusedUnchanged                // the page already is what the write would make it
	refusedFull                     // the wiki would hold more than storage-max: 507 (checkRoom)
	refusedReadOnly                 // a viewer changing something shared, an editor a guide: 403 (readOnlyWhy)
	refusedConflict                 // what the change started from has changed since: 409
	refusedTooLarge                 // a request body over its limit: 413
)

// status: the HTTP status that says a refusal of this kind.
func (k refusal) status() int {
	switch k {
	case refusedNotFound:
		return http.StatusNotFound
	case refusedFull:
		return http.StatusInsufficientStorage
	case refusedReadOnly:
		return http.StatusForbidden
	case refusedConflict:
		return http.StatusConflict
	case refusedTooLarge:
		return http.StatusRequestEntityTooLarge
	}
	return http.StatusBadRequest
}

// refusalError: an operation refused, with a message for the user.
type refusalError struct {
	kind refusal
	msg  string
}

func (e *refusalError) Error() string { return e.msg }

func refuse(msg string) error              { return &refusalError{refusedInvalid, msg} }
func refuseAs(k refusal, msg string) error { return &refusalError{k, msg} }

// refusalOf: err's refusal, if it is one.
func refusalOf(err error) (*refusalError, bool) {
	var re *refusalError
	ok := errors.As(err, &re)
	return re, ok
}

// errNotFound: no such task, or not one the user may see.
var errNotFound = refuseAs(refusedNotFound, "Not Found")
