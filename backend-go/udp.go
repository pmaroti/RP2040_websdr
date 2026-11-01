package main

import (
	"context"
	"errors"
	"log"
	"math"
	"math/rand"
	"net"
	"time"
)

// receiveUDP reads sample datagrams from conn into buf until ctx ends.
func receiveUDP(ctx context.Context, conn *net.UDPConn, buf *SampleBuffer) {
	go func() { <-ctx.Done(); conn.Close() }()
	p := make([]byte, 2048)
	for {
		n, _, err := conn.ReadFromUDP(p)
		if err != nil {
			if ctx.Err() != nil || errors.Is(err, net.ErrClosed) {
				return
			}
			continue // e.g. ICMP port unreachable surfacing as a read error
		}
		if seq, rate, flags, x, ok := parseDatagram(p[:n]); ok {
			buf.Add(seq, rate, flags, x)
		}
	}
}

// subscribe asks the Pico to stream to us while no data arrives. Any
// datagram to the Pico's control port (7777) points the stream at the
// sender's address and port. It is sent from the receiving socket, so the
// stream comes back to it. The Pico also sends the samples from port 7777,
// which makes them replies to this datagram as far as a NAT is concerned:
// that is what lets the stream reach a container on a Docker bridge network.
func subscribe(ctx context.Context, conn *net.UDPConn, pico *net.UDPAddr, buf *SampleBuffer) {
	t := time.NewTicker(2 * time.Second)
	defer t.Stop()
	for {
		if !buf.Streaming() {
			_, _ = conn.WriteToUDP([]byte("hello"), pico) // fails while the USB network is down
		}
		select {
		case <-ctx.Done():
			return
		case <-t.C:
		}
	}
}

// demoSource generates a test signal shaped like the Pico's stream, in real
// time (one 500-sample block every 20 ms at 25 kHz). Besides the steady
// tones of the Python demo it carries signals to try the demodulator on:
//
//	1 kHz      strong steady tone
//	3.333 kHz  weaker steady tone
//	2-10 kHz   weak slow sweep
//	5 kHz      AM carrier, 60 % modulated with a little tune
//	9 kHz      USB signal: carrier suppressed, tones 600 Hz / 1.5 kHz above it
//	11 kHz     LSB signal: tones 800 Hz / 1.2 kHz below it
func demoSource(ctx context.Context, buf *SampleBuffer, rate, block int) {
	tune := []float64{523.25, 587.33, 659.25, 698.46, 783.99, 698.46, 659.25, 587.33}
	rng := rand.New(rand.NewSource(1))
	var seq uint32
	var n0 int64
	next := time.Now()
	start := time.Now()
	ph := map[string]float64{}
	osc := func(name string, f float64) float64 {
		ph[name] = math.Mod(ph[name]+2*math.Pi*f/float64(rate), 2*math.Pi)
		return math.Sin(ph[name])
	}
	for {
		x := make([]float32, block)
		el := time.Since(start).Seconds()
		sweep := 6000 + 4000*math.Sin(2*math.Pi*0.05*el)
		note := tune[int(el/0.4)%len(tune)]
		beep := int(el/0.8) % 2
		for i := range x {
			t := float64(n0+int64(i)) / float64(rate)
			v := 0.3*math.Sin(2*math.Pi*1000*t) +
				0.05*math.Sin(2*math.Pi*3333*t) +
				0.02*osc("sweep", sweep)
			// AM: carrier * (1 + m * audio)
			v += 0.12 * math.Sin(2*math.Pi*5000*t) * (1 + 0.6*osc("note", note))
			// SSB: only the tones themselves, offset from the (absent) carrier.
			if beep == 0 {
				v += 0.08*math.Sin(2*math.Pi*(9000+600)*t) + 0.06*math.Sin(2*math.Pi*(11000-800)*t)
			} else {
				v += 0.08*math.Sin(2*math.Pi*(9000+1500)*t) + 0.06*math.Sin(2*math.Pi*(11000-1200)*t)
			}
			v += 0.002 * rng.NormFloat64()
			// Quantise like the 12-bit ADC.
			code := math.Max(0, math.Min(4095, math.Round(2048+2047*v)))
			x[i] = float32((code - adcFullScale) / adcFullScale)
		}
		buf.Add(seq, rate, 0, x)
		seq++
		n0 += int64(block)
		// Sleep until the next block is due; scheduling against an absolute
		// time keeps the average rate exact even if a sleep runs late.
		next = next.Add(time.Duration(float64(block) / float64(rate) * float64(time.Second)))
		select {
		case <-ctx.Done():
			return
		case <-time.After(time.Until(next)):
		}
	}
}

func listenUDP(port int) (*net.UDPConn, error) {
	conn, err := net.ListenUDP("udp4", &net.UDPAddr{Port: port})
	if err != nil {
		return nil, err
	}
	_ = conn.SetReadBuffer(1 << 20)
	log.Printf("listening for samples on UDP %d", port)
	return conn, nil
}
