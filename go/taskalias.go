package main

import "zonewiki/internal/taskmodel"

// A task and its parts are package taskmodel's: its fields, its file's
// format, a series' dates. What the server adds is here and in
// taskstore.go: the files, the lock, the commits, who may see what.
type (
	Task         = taskmodel.Task
	Recurrence   = taskmodel.Recurrence
	HistoryEntry = taskmodel.HistoryEntry
	Comment      = taskmodel.Comment
)

// apiTask: a task as the API and the browser get it: as in its file and,
// with users, "private" (whether it is its user's own).
type apiTask struct{ *Task }

func (a apiTask) MarshalJSON() ([]byte, error) { return a.JSON(settings().Users != nil) }

// apiTasks: tasks as the API answers them.
func apiTasks(tasks []*Task) []apiTask {
	out := make([]apiTask, len(tasks))
	for i, t := range tasks {
		out[i] = apiTask{t}
	}
	return out
}
