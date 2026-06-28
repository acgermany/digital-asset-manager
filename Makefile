REGISTRY := 10.212.10.10:8083
VERSION  ?= $(shell git describe --tags --always --dirty 2>/dev/null || echo dev)

.PHONY: build push release

build:
	@echo "Building version: $(VERSION)"
	docker build --build-arg APP_VERSION=$(VERSION) \
	  -t $(REGISTRY)/dam-api:$(VERSION) -t $(REGISTRY)/dam-api:latest ./api
	docker build --build-arg VITE_APP_VERSION=$(VERSION) \
	  -t $(REGISTRY)/dam-frontend:$(VERSION) -t $(REGISTRY)/dam-frontend:latest ./frontend
	docker build --build-arg APP_VERSION=$(VERSION) \
	  -t $(REGISTRY)/dam-worker:$(VERSION) -t $(REGISTRY)/dam-worker:latest ./worker
	docker build \
	  -t $(REGISTRY)/dam-watcher:$(VERSION) -t $(REGISTRY)/dam-watcher:latest ./watcher

push:
	docker push $(REGISTRY)/dam-api:$(VERSION)
	docker push $(REGISTRY)/dam-api:latest
	docker push $(REGISTRY)/dam-frontend:$(VERSION)
	docker push $(REGISTRY)/dam-frontend:latest
	docker push $(REGISTRY)/dam-worker:$(VERSION)
	docker push $(REGISTRY)/dam-worker:latest
	docker push $(REGISTRY)/dam-watcher:$(VERSION)
	docker push $(REGISTRY)/dam-watcher:latest

release: build push
