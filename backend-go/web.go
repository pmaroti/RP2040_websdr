package main

import (
	"context"
	"encoding/binary"
	"encoding/json"
	"fmt"
	"io/fs"
	"log"
	"math"
	"net/http"
	"sync"
	"time"

	"github.com/gorilla/websocket"
)

// WebSocket endpoints:
//
// /ws  spectrum and status, same protocol as the Python backend, so the
//      old page works against this server too.
//   server -> page, text:   JSON status ("type": "status") about once a
//                           second and after every change, or
//                           {"type": "freq_error", "message": ...}
//   server -> page, binary: one spectrum, float32 little-endian
//                           [sample_rate, dB(bin 0), ..., dB(bin N/2)]
//   page -> server, text:   {"fft_size": 4096}, {"averaging": 0.5},
//                           {"squarewave": 7100000}
//
// /ws/audio  demodulated audio, one demodulator per connection
//   page -> server, text:   {"mode": "usb", "freq": 7101000, "bandwidth": 2700}
//                           mode off|am|lsb|usb; freq is RF while the LO
//                           runs, else IF; any field may be left out
//   server -> page, text:   {"type": "demod", ...} settings as applied, the
//                           IF passband, and the signal level, after each
//                           change and twice a second
//   server -> page, binary: audio, uint32 LE sample rate then int16 LE
//                           mono samples (one message per 20 ms datagram)

var upgrader = websocket.Upgrader{
	ReadBufferSize: 4096, WriteBufferSize: 1 << 16,
	// The page may be opened by hostname or IP from other machines.
	CheckOrigin: func(r *http.Request) bool { return true },
}

const writeWait = 5 * time.Second

type wsMsg struct {
	binary bool
	data   []byte
}

// client is one open spectrum page. Its writer goroutine owns all writes.
type client struct {
	conn *websocket.Conn
	send chan wsMsg
}

// Server owns the sample buffer, the spectrum, the square wave and the
// connected pages.
type Server struct {
	args   Args
	buf    *SampleBuffer
	spec   *Spectrum
	sqw    *SquareWave
	static fs.FS

	mu      sync.Mutex
	clients map[*client]struct{}
}

func NewServer(args Args, buf *SampleBuffer, static fs.FS) *Server {
	s := &Server{args: args, buf: buf, spec: NewSpectrum(), static: static, clients: map[*client]struct{}{}}
	s.sqw = NewSquareWave(args.Device, args.FreqPort, args.Freq, args.Demo, s.pushStatus)
	return s
}

func (s *Server) Handler() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("/ws", s.handleWS)
	mux.HandleFunc("/ws/audio", s.handleAudio)
	files := http.FileServer(http.FS(s.static))
	mux.Handle("/static/", http.StripPrefix("/static/", files))
	mux.HandleFunc("/", func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/" {
			http.NotFound(w, r)
			return
		}
		w.Header().Set("Cache-Control", "no-cache")
		http.ServeFileFS(w, r, s.static, "index.html")
	})
	return mux
}

type statusMsg struct {
	Type string `json:"type"`
	StreamStats
	Demo       bool             `json:"demo"`
	FFTSize    int              `json:"fft_size"`
	Averaging  float64          `json:"averaging"`
	FPS        float64          `json:"fps"`
	SquareWave SquareWaveStatus `json:"squarewave"`
	FFTSizes   []int            `json:"fft_sizes,omitempty"`
	Audio      bool             `json:"audio"` // this server offers /ws/audio
}

func (s *Server) status(withSizes bool) []byte {
	n, a := s.spec.Settings()
	m := statusMsg{Type: "status", StreamStats: s.buf.Stats(), Demo: s.args.Demo, FFTSize: n,
		Averaging: a, FPS: s.args.FPS, SquareWave: s.sqw.Status(), Audio: true}
	if withSizes {
		m.FFTSizes = fftSizes
	}
	b, _ := json.Marshal(m)
	return b
}

func (s *Server) broadcast(m wsMsg) {
	s.mu.Lock()
	defer s.mu.Unlock()
	for c := range s.clients {
		select {
		case c.send <- m:
		default: // page not keeping up: it misses this one
		}
	}
}

func (s *Server) pushStatus() { s.broadcast(wsMsg{data: s.status(false)}) }

func (s *Server) hasClients() bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	return len(s.clients) > 0
}

// SpectrumLoop computes and sends a spectrum FPS times per second, and a
// status message about once a second, while any page is open.
func (s *Server) SpectrumLoop(ctx context.Context) {
	t := time.NewTicker(time.Duration(float64(time.Second) / s.args.FPS))
	defer t.Stop()
	var lastStatus time.Time
	for {
		select {
		case <-ctx.Done():
			return
		case <-t.C:
		}
		if !s.hasClients() {
			continue
		}
		if time.Since(lastStatus) >= time.Second {
			lastStatus = time.Now()
			s.pushStatus()
		}
		if !s.buf.Streaming() {
			continue // no fresh data: the page keeps the last spectrum
		}
		n, _ := s.spec.Settings()
		x := s.buf.Latest(n)
		if x == nil {
			continue
		}
		db := s.spec.Compute(x)
		frame := make([]byte, 4*(len(db)+1))
		binary.LittleEndian.PutUint32(frame, math.Float32bits(float32(s.buf.SampleRate())))
		for i, v := range db {
			binary.LittleEndian.PutUint32(frame[4*(i+1):], math.Float32bits(v))
		}
		s.broadcast(wsMsg{binary: true, data: frame})
	}
}

func writeLoop(conn *websocket.Conn, send <-chan wsMsg, done <-chan struct{}) {
	ping := time.NewTicker(20 * time.Second)
	defer ping.Stop()
	for {
		select {
		case m := <-send:
			_ = conn.SetWriteDeadline(time.Now().Add(writeWait))
			typ := websocket.TextMessage
			if m.binary {
				typ = websocket.BinaryMessage
			}
			if err := conn.WriteMessage(typ, m.data); err != nil {
				conn.Close()
				return
			}
		case <-ping.C:
			_ = conn.SetWriteDeadline(time.Now().Add(writeWait))
			if err := conn.WriteMessage(websocket.PingMessage, nil); err != nil {
				conn.Close()
				return
			}
		case <-done:
			return
		}
	}
}

// keepAlive makes reads fail if the browser stops answering pings.
func keepAlive(conn *websocket.Conn) {
	_ = conn.SetReadDeadline(time.Now().Add(60 * time.Second))
	conn.SetPongHandler(func(string) error {
		return conn.SetReadDeadline(time.Now().Add(60 * time.Second))
	})
}

func (s *Server) handleWS(w http.ResponseWriter, r *http.Request) {
	conn, err := upgrader.Upgrade(w, r, nil)
	if err != nil {
		return
	}
	defer conn.Close()
	keepAlive(conn)
	c := &client{conn: conn, send: make(chan wsMsg, 16)}
	done := make(chan struct{})
	defer close(done)
	go writeLoop(conn, c.send, done)
	c.send <- wsMsg{data: s.status(true)} // first message also lists the FFT sizes
	s.mu.Lock()
	s.clients[c] = struct{}{}
	s.mu.Unlock()
	defer func() {
		s.mu.Lock()
		delete(s.clients, c)
		s.mu.Unlock()
	}()

	for {
		typ, data, err := conn.ReadMessage()
		if err != nil {
			return
		}
		if typ != websocket.TextMessage {
			continue
		}
		var cmd map[string]json.RawMessage
		if json.Unmarshal(data, &cmd) != nil {
			continue
		}
		if v, ok := cmd["fft_size"]; ok {
			var n float64
			if json.Unmarshal(v, &n) == nil {
				s.spec.SetFFTSize(int(n))
			}
		}
		if v, ok := cmd["averaging"]; ok {
			var a float64
			if json.Unmarshal(v, &a) == nil {
				s.spec.SetAveraging(a)
			}
		}
		if v, ok := cmd["squarewave"]; ok {
			hz, err := parseSquareWave(rawText(v))
			if err != nil {
				b, _ := json.Marshal(map[string]string{"type": "freq_error", "message": err.Error()})
				select {
				case c.send <- wsMsg{data: b}:
				default:
				}
				continue
			}
			// In the background, so this page keeps being served while we
			// talk to the Pico. Set() pushes the status to every page.
			go s.sqw.Set(hz)
			continue
		}
		s.pushStatus() // let every page see the new settings
	}
}

// rawText turns a JSON number or string into text for parseFreqText.
func rawText(v json.RawMessage) string {
	var str string
	if json.Unmarshal(v, &str) == nil {
		return str
	}
	var f float64
	if json.Unmarshal(v, &f) == nil {
		return fmt.Sprintf("%.3f", f)
	}
	return string(v)
}

// ---------------------------------------------------------------- audio

type demodRequest struct {
	Mode      *string          `json:"mode"`
	Freq      *json.RawMessage `json:"freq"`
	Bandwidth *float64         `json:"bandwidth"`
}

type demodStatus struct {
	Type      string   `json:"type"`
	Mode      string   `json:"mode"`
	Freq      *float64 `json:"freq"`
	Bandwidth float64  `json:"bandwidth"`
	BWMin     float64  `json:"bandwidth_min"`
	BWMax     float64  `json:"bandwidth_max"`
	IFFreq    *float64 `json:"if_freq"`
	IFLow     *float64 `json:"if_low"`
	IFHigh    *float64 `json:"if_high"`
	Inverted  bool     `json:"inverted"`
	ViaRF     bool     `json:"via_rf"`
	LO        float64  `json:"lo"`
	AudioRate float64  `json:"audio_rate"`
	SignalDB  *float64 `json:"signal_db"`
	Error     string   `json:"error"`
}

// audioSession is one /ws/audio connection: what the listener asked for,
// and their demodulator.
type audioSession struct {
	mode string
	freq *float64 // as requested: RF with the LO on, else IF
	bw   float64
	err  string

	dem    *Demodulator
	tuning Tuning
	lo     float64
	active bool
}

// apply re-resolves the tuning (the LO may have changed) and configures the
// demodulator.
func (a *audioSession) apply(rate int, lo float64) {
	a.lo, a.active, a.err = lo, false, ""
	if a.mode == ModeOff || a.freq == nil || rate <= 0 {
		return
	}
	if a.dem == nil || a.dem.InRate != rate {
		a.dem = NewDemodulator(rate)
	}
	lo2, hi := bandwidthLimits(a.mode, a.dem.OutRate)
	a.bw = math.Min(math.Max(a.bw, lo2), hi)
	t, err := resolveTuning(a.mode, *a.freq, lo, rate)
	if err != nil {
		a.err = err.Error()
		return
	}
	a.tuning, a.active = t, true
	a.dem.Configure(t.IFMode, t.IFHz, a.bw)
}

func (a *audioSession) status() []byte {
	st := demodStatus{Type: "demod", Mode: a.mode, Freq: a.freq, Bandwidth: a.bw, LO: a.lo, Error: a.err}
	if a.dem != nil {
		st.AudioRate = a.dem.OutRate
		st.BWMin, st.BWMax = bandwidthLimits(a.mode, a.dem.OutRate)
	}
	if a.active {
		f := a.tuning.IFHz
		lo, hi := passband(a.tuning.IFMode, f, a.bw)
		sig := a.dem.SignalDB()
		st.IFFreq, st.IFLow, st.IFHigh, st.SignalDB = &f, &lo, &hi, &sig
		st.Inverted, st.ViaRF = a.tuning.Inverted, a.tuning.ViaRF
	}
	b, _ := json.Marshal(st)
	return b
}

func (s *Server) handleAudio(w http.ResponseWriter, r *http.Request) {
	conn, err := upgrader.Upgrade(w, r, nil)
	if err != nil {
		return
	}
	defer conn.Close()
	keepAlive(conn)
	log.Printf("audio listener connected from %s", r.RemoteAddr)
	defer log.Printf("audio listener %s left", r.RemoteAddr)

	// Reader goroutine: settings from the page.
	reqs := make(chan demodRequest, 8)
	gone := make(chan struct{})
	go func() {
		defer close(gone)
		for {
			typ, data, err := conn.ReadMessage()
			if err != nil {
				return
			}
			var req demodRequest
			if typ == websocket.TextMessage && json.Unmarshal(data, &req) == nil {
				select {
				case reqs <- req:
				default:
				}
			}
		}
	}()

	blocks := s.buf.Subscribe()
	defer s.buf.Unsubscribe(blocks)
	sess := &audioSession{mode: ModeOff, bw: defaultBandwidth(ModeAM)}
	write := func(typ int, b []byte) bool {
		_ = conn.SetWriteDeadline(time.Now().Add(writeWait))
		return conn.WriteMessage(typ, b) == nil
	}
	rate := s.buf.SampleRate()
	sess.apply(rate, s.sqw.LO())
	if !write(websocket.TextMessage, sess.status()) {
		return
	}
	tick := time.NewTicker(500 * time.Millisecond)
	defer tick.Stop()
	ping := time.NewTicker(20 * time.Second)
	defer ping.Stop()
	for {
		select {
		case <-gone:
			return
		case req := <-reqs:
			if req.Mode != nil && validMode(*req.Mode) {
				if *req.Mode != sess.mode && req.Bandwidth == nil {
					sess.bw = defaultBandwidth(*req.Mode)
				}
				sess.mode = *req.Mode
			}
			parseErr := ""
			if req.Freq != nil {
				if hz, err := parseFreqText(rawText(*req.Freq)); err == nil {
					sess.freq = &hz
				} else {
					parseErr = err.Error()
				}
			}
			if req.Bandwidth != nil && *req.Bandwidth > 0 {
				sess.bw = *req.Bandwidth
			}
			sess.apply(s.buf.SampleRate(), s.sqw.LO())
			if parseErr != "" {
				sess.err = parseErr
			}
			if !write(websocket.TextMessage, sess.status()) {
				return
			}
		case b := <-blocks:
			if lo := s.sqw.LO(); lo != sess.lo || b.Rate != rate {
				rate = b.Rate
				sess.apply(rate, lo)
				if !write(websocket.TextMessage, sess.status()) {
					return
				}
			}
			if !sess.active {
				continue
			}
			pcm := sess.dem.Process(b.Samples)
			msg := make([]byte, 4+2*len(pcm))
			binary.LittleEndian.PutUint32(msg, uint32(math.Round(sess.dem.OutRate)))
			for i, v := range pcm {
				binary.LittleEndian.PutUint16(msg[4+2*i:], uint16(v))
			}
			if !write(websocket.BinaryMessage, msg) {
				return
			}
		case <-tick.C:
			if !write(websocket.TextMessage, sess.status()) {
				return
			}
		case <-ping.C:
			if !write(websocket.PingMessage, nil) {
				return
			}
		}
	}
}
