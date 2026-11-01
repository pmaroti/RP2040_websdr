package main

import (
	"encoding/binary"
	"math"
	"sync"
	"time"
)

// Sample datagram layout, same as packet_header_t in the firmware (all
// little-endian): uint32 magic, uint32 seq, uint32 sample rate, uint16 count,
// uint16 flags, then count uint16 ADC codes.
const (
	headerSize   = 16
	packetMagic  = 0x31434441 // "ADC1"
	flagOverrun  = 0x0001
	adcFullScale = 2048.0 // 12-bit ADC, mid-scale = 0
	ringCapacity = 1 << 16
)

// Block is one datagram's worth of samples, scaled to -1..+1, handed to every
// subscriber (the demodulators) as it arrives.
type Block struct {
	Rate    int
	Samples []float32
}

// parseDatagram checks and decodes one sample datagram. ok is false for
// anything that is not a well-formed datagram, which the caller ignores.
func parseDatagram(p []byte) (seq uint32, rate int, flags uint16, x []float32, ok bool) {
	if len(p) < headerSize || binary.LittleEndian.Uint32(p[0:]) != packetMagic {
		return
	}
	seq = binary.LittleEndian.Uint32(p[4:])
	rate = int(binary.LittleEndian.Uint32(p[8:]))
	count := int(binary.LittleEndian.Uint16(p[12:]))
	flags = binary.LittleEndian.Uint16(p[14:])
	if len(p) != headerSize+2*count || rate <= 0 {
		return
	}
	x = make([]float32, count)
	for i := range x {
		code := binary.LittleEndian.Uint16(p[headerSize+2*i:])
		x[i] = (float32(code) - adcFullScale) / adcFullScale
	}
	return seq, rate, flags, x, true
}

// SampleBuffer keeps the most recent samples (for the spectrum), the stream
// statistics, and the list of subscribers that get every new block (the
// audio demodulators). Safe for concurrent use.
type SampleBuffer struct {
	mu          sync.Mutex
	data        []float32
	writePos    int
	total       int64
	sampleRate  int
	expectedSeq uint32
	haveSeq     bool
	lostPackets uint64
	overruns    uint64
	packets     uint64
	lastPacket  time.Time
	subs        map[chan Block]struct{}
}

func NewSampleBuffer() *SampleBuffer {
	return &SampleBuffer{data: make([]float32, ringCapacity), subs: map[chan Block]struct{}{}}
}

// Add stores one datagram's samples, updates the statistics and passes the
// block on to the subscribers. A subscriber that is not keeping up loses the
// block instead of stalling the receiver.
func (b *SampleBuffer) Add(seq uint32, rate int, flags uint16, x []float32) {
	b.mu.Lock()
	b.sampleRate = rate
	// A gap in sequence numbers means lost datagrams. Done modulo 2^32 so a
	// wrapping counter is not mistaken for a gap; a big backwards jump means
	// the Pico rebooted, and we just resync.
	if b.haveSeq && seq != b.expectedSeq {
		if gap := seq - b.expectedSeq; gap < 0x80000000 {
			b.lostPackets += uint64(gap)
		}
	}
	b.expectedSeq, b.haveSeq = seq+1, true
	if flags&flagOverrun != 0 {
		b.overruns++
	}
	for _, v := range x {
		b.data[b.writePos] = v
		b.writePos = (b.writePos + 1) % ringCapacity
	}
	b.total += int64(len(x))
	b.packets++
	b.lastPacket = time.Now()
	blk := Block{Rate: rate, Samples: x}
	for ch := range b.subs {
		select {
		case ch <- blk:
		default:
		}
	}
	b.mu.Unlock()
}

// Latest returns a copy of the newest n samples in time order, or nil if
// fewer than n have arrived.
func (b *SampleBuffer) Latest(n int) []float32 {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.latestLocked(n)
}

func (b *SampleBuffer) latestLocked(n int) []float32 {
	if n <= 0 || n > ringCapacity || b.total < int64(n) {
		return nil
	}
	out := make([]float32, n)
	start := (b.writePos - n + ringCapacity) % ringCapacity
	k := copy(out, b.data[start:])
	if k < n {
		copy(out[k:], b.data[:n-k])
	}
	return out
}

// Streaming is true if a datagram arrived within the last second.
func (b *SampleBuffer) Streaming() bool {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.streamingLocked()
}

func (b *SampleBuffer) streamingLocked() bool {
	return !b.lastPacket.IsZero() && time.Since(b.lastPacket) < time.Second
}

// SampleRate is the rate from the last datagram, 0 before the first one.
func (b *SampleBuffer) SampleRate() int {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.sampleRate
}

// Subscribe returns a channel that receives every new block until
// Unsubscribe is called with it.
func (b *SampleBuffer) Subscribe() chan Block {
	ch := make(chan Block, 64)
	b.mu.Lock()
	b.subs[ch] = struct{}{}
	b.mu.Unlock()
	return ch
}

func (b *SampleBuffer) Unsubscribe(ch chan Block) {
	b.mu.Lock()
	delete(b.subs, ch)
	b.mu.Unlock()
}

// StreamStats is the stream part of the status message.
type StreamStats struct {
	Streaming   bool     `json:"streaming"`
	SampleRate  *int     `json:"sample_rate"`
	Packets     uint64   `json:"packets"`
	LostPackets uint64   `json:"lost_packets"`
	Overruns    uint64   `json:"overruns"`
	MinV        *float64 `json:"min_v"`
	MaxV        *float64 `json:"max_v"`
}

// Stats returns the statistics, with the input voltage range taken from the
// last second of samples (or fewer at startup).
func (b *SampleBuffer) Stats() StreamStats {
	b.mu.Lock()
	defer b.mu.Unlock()
	s := StreamStats{Streaming: b.streamingLocked(), Packets: b.packets,
		LostPackets: b.lostPackets, Overruns: b.overruns}
	if b.sampleRate > 0 {
		r := b.sampleRate
		s.SampleRate = &r
	}
	if b.total > 0 {
		n := b.sampleRate
		if n <= 0 {
			n = 25000
		}
		if int64(n) > b.total {
			n = int(b.total)
		}
		if lvl := b.latestLocked(n); lvl != nil {
			lo, hi := math.Inf(1), math.Inf(-1)
			for _, v := range lvl {
				lo, hi = math.Min(lo, float64(v)), math.Max(hi, float64(v))
			}
			// -1..+1 back to the ADC code, then to volts (3.3 V reference).
			toV := func(v float64) float64 { return (v*adcFullScale + adcFullScale) * 3.3 / 4095 }
			minV, maxV := toV(lo), toV(hi)
			s.MinV, s.MaxV = &minV, &maxV
		}
	}
	return s
}
