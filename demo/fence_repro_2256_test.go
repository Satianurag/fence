package trailers

import (
	"os/exec"
	"strings"
	"testing"
)

// Repro for entireio/cli#2256: after AppendCheckpointTrailer, git itself must see the new trailer.
func TestFenceRepro2256(t *testing.T) {
	msg := "Message\n\nBody.\n\n  Entire-Checkpoint: a1b2c3d4e5f6\n"
	out := AppendCheckpointTrailer(msg, "b2c3d4e5f6a7")
	cmd := exec.Command("git", "interpret-trailers", "--parse")
	cmd.Stdin = strings.NewReader(out)
	parsed, err := cmd.Output()
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(parsed), "Entire-Checkpoint: b2c3d4e5f6a7") {
		t.Fatalf("git does not see the appended trailer.\nmessage:\n%q\ngit parsed:\n%q", out, parsed)
	}
}
