docker rm --force rc-api-local
docker image rm rc-api-local
sudo docker build -t rc-api-local -f Dockerfile.local .
docker run -d -p 8080:8080 --name rc-api-local rc-api-local