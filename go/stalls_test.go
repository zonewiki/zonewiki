package main

import (
	"bufio"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

// stallServer: a server behind noStalls with a short stallTime, and what
// its routes found, one result per request.
func stallServer(t *testing.T) (addr string, results chan error) {
	t.Helper()
	old := stallTime
	stallTime = 300 * time.Millisecond
	results = make(chan error, 10)
	mux := http.NewServeMux()
	mux.HandleFunc("/read", func(w http.ResponseWriter, r *http.Request) {
		b, err := io.ReadAll(r.Body)
		if err == nil {
			_, err = fmt.Fprintf(w, "%d bytes", len(b))
		}
		results <- err
	})
	mux.HandleFunc("/big", func(w http.ResponseWriter, r *http.Request) {
		chunk := make([]byte, 1<<20)
		var err error
		for i := 0; i < 256 && err == nil; i++ {
			_, err = w.Write(chunk)
		}
		results <- err
	})
	mux.HandleFunc("/slow", func(w http.ResponseWriter, r *http.Request) {
		_, _ = io.ReadAll(r.Body)
		time.Sleep(3 * stallTime)
		if err := r.Context().Err(); err != nil {
			results <- err
			return
		}
		_, err := io.WriteString(w, "done")
		results <- err
	})
	mux.HandleFunc("/empty", func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusNoContent)
		results <- nil
	})
	srv := httptest.NewServer(noStalls(mux))
	t.Cleanup(func() {
		srv.CloseClientConnections()
		srv.Close()
		stallTime = old
	})
	return srv.Listener.Addr().String(), results
}

func result(t *testing.T, results chan error) error {
	t.Helper()
	select {
	case err := <-results:
		return err
	case <-time.After(5 * time.Second):
		t.Fatal("the route is still waiting for the client")
		return nil
	}
}

func isTimeout(err error) bool {
	ne, ok := err.(net.Error)
	return ok && ne.Timeout()
}

func TestABodyThatStopsComingFails(t *testing.T) {
	addr, results := stallServer(t)
	conn, err := net.Dial("tcp", addr)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	fmt.Fprint(conn, "POST /read HTTP/1.1\r\nHost: x\r\nContent-Length: 10\r\n\r\nabc")
	if err := result(t, results); !isTimeout(err) {
		t.Fatalf("got %v, want a timeout", err)
	}
	// And the connection is closed, not kept for the rest.
	_ = conn.SetReadDeadline(time.Now().Add(5 * time.Second))
	if _, err := io.ReadAll(conn); err != nil {
		t.Fatalf("the connection stays open: %v", err)
	}
}

func TestABodyNeverReadDoesntHoldTheConnection(t *testing.T) {
	addr, results := stallServer(t)
	conn, err := net.Dial("tcp", addr)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	fmt.Fprint(conn, "POST /empty HTTP/1.1\r\nHost: x\r\nContent-Length: 10\r\n\r\nabc")
	if err := result(t, results); err != nil {
		t.Fatal(err)
	}
	_ = conn.SetReadDeadline(time.Now().Add(5 * time.Second))
	if _, err := io.ReadAll(conn); err != nil {
		t.Fatalf("the connection stays open: %v", err)
	}
}

func TestASlowBodyThatKeepsComingIsRead(t *testing.T) {
	addr, results := stallServer(t)
	conn, err := net.Dial("tcp", addr)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	fmt.Fprint(conn, "POST /read HTTP/1.1\r\nHost: x\r\nContent-Length: 8\r\n\r\n")
	for i := 0; i < 8; i++ { // 800 ms in all, never 300 ms without a byte
		time.Sleep(100 * time.Millisecond)
		fmt.Fprint(conn, "x")
	}
	if err := result(t, results); err != nil {
		t.Fatal(err)
	}
	resp, err := http.ReadResponse(bufio.NewReader(conn), nil)
	if err != nil {
		t.Fatal(err)
	}
	if b, _ := io.ReadAll(resp.Body); string(b) != "8 bytes" {
		t.Fatalf("got %q", b)
	}
}

func TestAResponseNobodyReadsFails(t *testing.T) {
	addr, results := stallServer(t)
	conn, err := net.Dial("tcp", addr)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	fmt.Fprint(conn, "GET /big HTTP/1.1\r\nHost: x\r\n\r\n")
	if err := result(t, results); !isTimeout(err) {
		t.Fatalf("got %v, want a timeout", err)
	}
}

func TestAResponseReadSlowlyArrivesWhole(t *testing.T) {
	addr, results := stallServer(t)
	resp, err := http.Get("http://" + addr + "/big")
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	n, buf := 0, make([]byte, 16<<20)
	for {
		m, err := io.ReadFull(resp.Body, buf)
		n += m
		if err != nil {
			break
		}
		time.Sleep(20 * time.Millisecond) // 16 pauses: longer in all than stallTime
	}
	if err := result(t, results); err != nil || n != 256<<20 {
		t.Fatalf("%d bytes, %v", n, err)
	}
}

func TestASlowRouteIsNotAStall(t *testing.T) {
	// The route takes longer than stallTime, after the body and before
	// the response; and so does the pause between two requests on one
	// connection, whose second answers without a body.
	addr, results := stallServer(t)
	client := &http.Client{}
	for _, body := range []string{"", "some text"} {
		resp, err := client.Post("http://"+addr+"/slow", "text/plain", strings.NewReader(body))
		if err != nil {
			t.Fatal(err)
		}
		b, _ := io.ReadAll(resp.Body)
		resp.Body.Close()
		if err := result(t, results); err != nil || string(b) != "done" {
			t.Fatalf("%q, %v", b, err)
		}
	}
	time.Sleep(2 * stallTime)
	resp, err := client.Get("http://" + addr + "/empty")
	if err != nil || resp.StatusCode != http.StatusNoContent {
		t.Fatalf("%v, %v", resp, err)
	}
	resp.Body.Close()
}
