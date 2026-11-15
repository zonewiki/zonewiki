package main

import "zonewiki/internal/taskmodel"

// A task and its parts are package taskmodel's: its fields, its file's
// format, a series' dates. What the server adds is here and in
// tasklogic.go and package taskfiles: the files, the lock, the commits, who
// may see what.
type (
	Task         = taskmodel.Task
	Recurrence   = taskmodel.Recurrence
	HistoryEntry = taskmodel.HistoryEntry
	Comment      = taskmodel.Comment
)

// apiTask: a task as the API and the browser get it: as in its file and,
// with users, "private" (whether it is its user's own). Without users it
// has no assignee, whatever its file says.
type apiTask struct{ *Task }

func (a apiTask) MarshalJSON() ([]byte, error) {
	if settings().Users != nil {
		// created_by for every task, also one from before the field (Maker).
		if maker := a.Maker(); maker != a.CreatedBy {
			t := *a.Task
			t.CreatedBy = maker
			return t.JSON(true)
		}
		return a.JSON(true)
	}
	if a.Assignee == "" {
		return a.JSON(false)
	}
	t := *a.Task
	t.Assignee = ""
	return t.JSON(false)
}

// apiTasks: tasks as the API answers them.
func apiTasks(tasks []*Task) []apiTask {
	out := make([]apiTask, len(tasks))
	for i, t := range tasks {
		out[i] = apiTask{t}
	}
	return out
}
