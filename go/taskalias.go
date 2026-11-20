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
		// In its history who looked at it (historyWithSeen).
		t := *a.Task
		t.History = historyWithSeen(a.Task)
		if len(t.Followers) > 0 {
			t.Followers = followersSeen(a.Task) // those who see it: the others are told nothing
		}
		return t.JSON(true)
	}
	if a.Assignee == "" && len(a.Followers) == 0 {
		return a.JSON(false)
	}
	t := *a.Task
	t.Assignee, t.Followers = "", nil
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
