#!/bin/bash
services=(
  "nodejs-web 3000 80"
  "python-api 8000 80"
  "java-spring 8080 80"
  "postgres-db 5432 5432"
  "redis-cache 6379 6379"
)

for svc in "${services[@]}"; do
  read name localport remoteport <<< "$svc"
  echo "Port-forwarding $name $localport:$remoteport"
  nohup kubectl port-forward svc/$name $localport:$remoteport > $name.log 2>&1 &
done