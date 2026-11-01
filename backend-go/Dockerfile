# syntax=docker/dockerfile:1
#
# Multi-stage build for the Go backend.
#   stage 1 (build): full Go toolchain, runs the tests, compiles a static binary
#   stage 2 (final): distroless, just the binary (the web page is embedded in it)
#
#   docker build -t pico-websdr .
#   docker run --rm --network host pico-websdr            # Linux, real Pico
#   docker run --rm -p 8080:8080 pico-websdr --demo       # anywhere, test signal

ARG GO_VERSION=1.24

FROM golang:${GO_VERSION}-alpine AS build
WORKDIR /src
# Dependencies first, so they stay cached while only the code changes.
COPY go.mod go.sum ./
RUN go mod download
COPY *.go ./
COPY static ./static
# CGO off: a fully static binary that runs on the distroless base.
ENV CGO_ENABLED=0
RUN go vet ./... && go test ./...
RUN go build -trimpath -ldflags="-s -w" -o /out/pico-websdr .

FROM gcr.io/distroless/static-debian12:nonroot
COPY --from=build /out/pico-websdr /usr/local/bin/pico-websdr
# 8080/tcp web page, 7778/udp sample stream from the Pico.
EXPOSE 8080/tcp 7778/udp
USER nonroot:nonroot
ENTRYPOINT ["/usr/local/bin/pico-websdr"]
