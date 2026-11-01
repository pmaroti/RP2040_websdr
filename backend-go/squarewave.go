package main

import (
	"bufio"
	"context"
	"fmt"
	"log"
	"math"
	"net"
	"regexp"
	"strconv"
	"strings"
	"sync"
	"time"
)

// PIO square wave limits at the default 125 MHz system clock (see
// squarewave.c). Min: slow 64-clock program at the largest divider. Max:
// 2-clock program at divider 1.
const (
	sqwMinHz = 125e6 / (64 * 65535.996)
	sqwMaxHz = 125e6 / 2
)

var freqRe = regexp.MustCompile(`^([0-9]*\.?[0-9]+)(m|k)?(hz)?$`)

// parseFreqText reads "7.1M", "7100 kHz", "7100000" etc. into Hz, without a
// range check. "off" and "0" give 0.
func parseFreqText(text string) (float64, error) {
	t := strings.ReplaceAll(strings.ToLower(strings.TrimSpace(text)), " ", "")
	if t == "off" || t == "0" {
		return 0, nil
	}
	m := freqRe.FindStringSubmatch(t)
	if m == nil {
		return 0, fmt.Errorf("can't read '%s', try 7.1M, 7100k or 7100000", text)
	}
	hz, _ := strconv.ParseFloat(m[1], 64)
	switch m[2] {
	case "m":
		hz *= 1e6
	case "k":
		hz *= 1e3
	}
	return hz, nil
}

// parseSquareWave is parseFreqText plus the square wave's range check.
func parseSquareWave(text string) (float64, error) {
	hz, err := parseFreqText(text)
	if err != nil {
		return 0, err
	}
	if hz != 0 && (hz < sqwMinHz || hz > sqwMaxHz) {
		return 0, fmt.Errorf("out of range, %.0f Hz to %.1f MHz", sqwMinHz, sqwMaxHz/1e6)
	}
	return hz, nil
}

// simulatedPicoFreq is what the firmware would produce for hz (same divider
// maths as squarewave.c). Used by --demo, which has no Pico to ask.
func simulatedPicoFreq(hz float64) float64 {
	if hz == 0 {
		return 0
	}
	cycles := 2.0
	if hz < 10000 {
		cycles = 64
	}
	div := math.Floor(125e6/(cycles*hz)*256 + 0.5)
	div = math.Min(math.Max(div, 256), 0xFFFFFF)
	return 125e6 * 256 / (cycles * div)
}

// SquareWaveStatus is the "squarewave" part of the status message.
type SquareWaveStatus struct {
	Requested *float64 `json:"requested"` // Hz, 0 = off, nil = leave the Pico alone
	Actual    *float64 `json:"actual"`    // what the Pico reported, nil = unknown
	State     string   `json:"state"`     // connecting | ok | error | offline
	Message   string   `json:"message"`
}

// SquareWave keeps the Pico's square wave (the receiver's local oscillator)
// at the requested frequency. It talks to the firmware's line-based TCP
// server; every two seconds it asks for the current frequency and re-sends
// the requested one if they differ, which covers startup, replugging and
// Pico resets.
type SquareWave struct {
	addr     string
	demo     bool
	onChange func()

	cmdMu sync.Mutex // one TCP conversation at a time

	mu        sync.Mutex
	requested *float64
	actual    *float64
	applied   *[2]float64 // (requested, actual) of the last successful set
	state     string
	message   string
}

func NewSquareWave(device string, port int, initial *float64, demo bool, onChange func()) *SquareWave {
	return &SquareWave{addr: net.JoinHostPort(device, strconv.Itoa(port)), demo: demo,
		onChange: onChange, requested: initial, state: "connecting"}
}

func (s *SquareWave) Status() SquareWaveStatus {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.statusLocked()
}

func (s *SquareWave) statusLocked() SquareWaveStatus {
	return SquareWaveStatus{Requested: copyPtr(s.requested), Actual: copyPtr(s.actual),
		State: s.state, Message: s.message}
}

// LO returns the frequency the Pico produces right now, or 0 if it is off or
// unknown.
func (s *SquareWave) LO() float64 {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.state == "ok" && s.actual != nil {
		return *s.actual
	}
	return 0
}

func copyPtr(p *float64) *float64 {
	if p == nil {
		return nil
	}
	v := *p
	return &v
}

// command sends one line and returns the Pico's one-line reply.
func (s *SquareWave) command(line string) (string, error) {
	if s.demo {
		time.Sleep(50 * time.Millisecond)
		if line == "?" {
			s.mu.Lock()
			a := 0.0
			if s.actual != nil {
				a = *s.actual
			}
			s.mu.Unlock()
			return fmt.Sprintf("FREQ %.3f", a), nil
		}
		hz, _ := strconv.ParseFloat(line, 64)
		return fmt.Sprintf("OK %.3f", simulatedPicoFreq(hz)), nil
	}
	// One short-lived connection per command: connect, read the greeting,
	// send the command, read the reply, close. 2 s limit on the whole thing
	// so an unplugged Pico can't hang us.
	conn, err := net.DialTimeout("tcp", s.addr, 2*time.Second)
	if err != nil {
		return "", err
	}
	defer conn.Close()
	_ = conn.SetDeadline(time.Now().Add(4 * time.Second))
	r := bufio.NewReader(conn)
	if _, err := r.ReadString('\n'); err != nil { // greeting
		return "", err
	}
	if _, err := conn.Write([]byte(line + "\n")); err != nil {
		return "", err
	}
	reply, err := r.ReadString('\n')
	if err != nil {
		return "", err
	}
	return strings.TrimSpace(reply), nil
}

func replyHz(reply string) (float64, error) {
	f := strings.Fields(reply)
	if len(f) < 2 {
		return 0, fmt.Errorf("bad reply %q", reply)
	}
	return strconv.ParseFloat(f[1], 64)
}

// sync asks the Pico what it is doing and corrects it if needed.
func (s *SquareWave) sync() {
	s.cmdMu.Lock()
	defer s.cmdMu.Unlock()
	before := s.Status()
	err := func() error {
		reply, err := s.command("?")
		if err != nil {
			return err
		}
		var current *float64
		if strings.HasPrefix(reply, "FREQ") {
			v, err := replyHz(reply)
			if err != nil {
				return err
			}
			current = &v
		}
		s.mu.Lock()
		s.actual = current
		want := copyPtr(s.requested)
		applied := s.applied
		s.mu.Unlock()
		// Re-send if we never set this value, or the Pico no longer runs it
		// (e.g. it was reset and came back with the output off). Compare
		// with what the Pico reported when we set it, since the hardware can
		// only approximate most frequencies.
		if want != nil && (current == nil || applied == nil || applied[0] != *want ||
			math.Abs(*current-applied[1]) > 0.5) {
			reply, err := s.command(fmt.Sprintf("%.3f", *want))
			if err != nil {
				return err
			}
			s.mu.Lock()
			defer s.mu.Unlock()
			if strings.HasPrefix(reply, "OK") {
				v, err := replyHz(reply)
				if err != nil {
					return err
				}
				s.actual = &v
				s.applied = &[2]float64{*want, v}
				s.state, s.message = "ok", ""
				log.Printf("square wave set to %.3f Hz", v)
			} else {
				s.state, s.message = "error", strings.TrimSpace(strings.TrimPrefix(reply, "ERR"))
				s.requested = nil // don't retry a value the Pico rejects
				log.Printf("Pico refused frequency %.3f: %s", *want, reply)
			}
			return nil
		}
		s.mu.Lock()
		s.state, s.message = "ok", ""
		s.mu.Unlock()
		return nil
	}()
	if err != nil {
		s.mu.Lock()
		if s.state != "offline" {
			log.Printf("Pico frequency port %s not reachable, will retry (%v)", s.addr, err)
		}
		s.state, s.message, s.actual = "offline", "Pico not reachable", nil
		s.mu.Unlock()
	}
	if after := s.Status(); !sameStatus(before, after) {
		s.onChange()
	}
}

func sameStatus(a, b SquareWaveStatus) bool {
	eq := func(x, y *float64) bool { return (x == nil && y == nil) || (x != nil && y != nil && *x == *y) }
	return eq(a.Requested, b.Requested) && eq(a.Actual, b.Actual) && a.State == b.State && a.Message == b.Message
}

// Set is called when a user sets a new frequency on the page.
func (s *SquareWave) Set(hz float64) {
	s.mu.Lock()
	s.requested = &hz
	s.state, s.message = "connecting", ""
	s.mu.Unlock()
	s.onChange() // show "talking to Pico..." right away
	s.sync()
}

// Run checks every two seconds until ctx ends.
func (s *SquareWave) Run(ctx context.Context) {
	for {
		s.sync()
		select {
		case <-ctx.Done():
			return
		case <-time.After(2 * time.Second):
		}
	}
}
