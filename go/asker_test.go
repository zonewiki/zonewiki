package main

import (
	"encoding/json"
	"go/ast"
	"go/parser"
	"go/token"
	"path/filepath"
	"reflect"
	"slices"
	"strings"
	"testing"
)

// Who asks is an asker (docs/plans/asker.md): the source is held to the
// three ways an asker comes to be, so that what a connection was given
// less is never dropped on the way to a permission question.
//
//   - A request's asker is made once, where its session or token is
//     checked (askerMakers); everything after takes it from userOf(r).
//   - asUser(name) is the person themselves, with all they may: only for
//     a user Zone knows by name and asks about (whom a task is given to,
//     who gets a mail, who loses a page in a move), in the functions listed
//     here. A new one is added to the list after a look at where its name
//     comes from: never from a request.
//   - Nothing makes an asker again from an asker's name (asUser(x.Name)),
//     which would give a narrowed asker everything back.
func TestAskersAreMadeInFewPlaces(t *testing.T) {
	// Where asUser is called, and for whom.
	asUserIn := []string{
		"applyAssignee",    // the new assignee: do they see the task's list
		"assigneeGone",     // a task's assignee: do they still see its list
		"canHaveNews",      // a user, by their seen-file: the tasks that can have news for them
		"followersSeen",    // a follower: do they see the task
		"isPrivateHome",    // a private folder's owner: is this page their home
		"listMembers",      // each user: is the list for them
		"logMentions",      // who is mentioned: do they see the task's list
		"losers",           // each user: who no longer sees a page after a move
		"placeSeers",       // each user: who sees a place
		"setListForOp",     // a list's assignees: whom a narrowed list would leave out
		"taskLosers",       // each user: who no longer sees a task in another list
		"taskRefsAsTitles", // a private page's owner: the tasks its text names, for the search index
		"tellAssigned",     // the assignee: do they see the task, before a mail
		"tellComment",      // who has or gave the task: do they see it, before a mail
		"tellMentioned",    // who was mentioned: do they see the task, before a mail
		"withUser",         // a session's user, or a visitor
		"mcpAuthorized",    // an MCP token's user: a token is of no connection
	}
	// Where an asker is written out as a literal with a name.
	askerMakers := []string{"connectionUser"}

	fset := token.NewFileSet()
	files, _ := filepath.Glob("*.go")
	var uses, literals []string
	for _, name := range files {
		if strings.HasSuffix(name, "_test.go") {
			continue
		}
		f, err := parser.ParseFile(fset, name, nil, 0)
		if err != nil {
			t.Fatal(err)
		}
		for _, d := range f.Decls {
			fn, ok := d.(*ast.FuncDecl)
			if !ok || fn.Body == nil || fn.Name.Name == "asUser" {
				continue
			}
			ast.Inspect(fn.Body, func(n ast.Node) bool {
				switch x := n.(type) {
				case *ast.CallExpr:
					if id, ok := x.Fun.(*ast.Ident); ok && id.Name == "asUser" && len(x.Args) == 1 {
						if !slices.Contains(uses, fn.Name.Name) {
							uses = append(uses, fn.Name.Name)
						}
						// asUser(user.Name), asUser(rq.user.Name), asUser(userOf(r).Name)
						if sel, ok := x.Args[0].(*ast.SelectorExpr); ok && sel.Sel.Name == "Name" && isRequestsAsker(sel.X) {
							t.Errorf("%s: an asker made again from an asker's name, which gives a narrowed one everything back", fset.Position(x.Pos()))
						}
					}
				case *ast.CompositeLit:
					if id, ok := x.Type.(*ast.Ident); ok && id.Name == "asker" && len(x.Elts) > 0 {
						if !slices.Contains(literals, fn.Name.Name) {
							literals = append(literals, fn.Name.Name)
						}
					}
				}
				return true
			})
		}
	}
	slices.Sort(uses)
	slices.Sort(asUserIn)
	if !reflect.DeepEqual(uses, asUserIn) {
		t.Errorf("asUser is called in\n  %v\nand the list here says\n  %v\nA new one: where does its name come from? Never from a request (userOf(r)).", uses, asUserIn)
	}
	if !reflect.DeepEqual(literals, askerMakers) {
		t.Errorf("an asker is written out with its fields in %v, and the list here says %v: a request's asker is made where its token is checked", literals, askerMakers)
	}
}

// isRequestsAsker: e is how the code names who asks: user, rq.user, userOf(r), rq.who().
func isRequestsAsker(e ast.Expr) bool {
	switch x := e.(type) {
	case *ast.Ident:
		return x.Name == "user"
	case *ast.SelectorExpr:
		return x.Sel.Name == "user"
	case *ast.CallExpr:
		switch f := x.Fun.(type) {
		case *ast.Ident:
			return f.Name == "userOf"
		case *ast.SelectorExpr:
			return f.Sel.Name == "who"
		}
	}
	return false
}

// An asker put where a name is written reads as the name.
func TestAnAskerIsWrittenAsItsName(t *testing.T) {
	a := asker{Name: "alice", ReadOnly: true}
	if b, _ := json.Marshal(map[string]any{"me": a}); string(b) != `{"me":"alice"}` {
		t.Errorf("in JSON: %s", b)
	}
	if s := "by " + a.String(); s != "by alice" {
		t.Error(s)
	}
}
