# SmartPacer Results Combining API (RC-API)

Get the collection in Postman for this API
https://www.getpostman.com/collections/b031477d48239fb260f4


## Deploying with Docker Compose
Begin by cloning this repository and all submodules with:
```
git clone --recurse-submodules https://github.gatech.edu/HDAP/SmartPacer-RC-API.git
```
This will pull down the core API and a modified CQF Ruler fork. The modified CQF Ruler fork provides for a streamlined r4 only deployment, but is otherwise the same as the base CQF Ruler repository.

The docker-compose.yml and CQF Ruler hapi.properties come pre-configured for a local deployment, all that is required to be set is the FHIR server you wish to execute the CQL against. This can be done in the docker-compose.yml via the "EXTERNAL_FHIR_SERVER_URL" and "EXTERNAL_FHIR_SERVER_AUTH" variables. The "EXTERNAL_FHIR_SERVER_AUTH" will be passed as a header in the requests to the server.

Please see the Advanced Configuration section below for more details on ports and other settings.

Once your environment variables are configured, you may build the images using:
```
docker-compose build
```
And then delpoy with:
```
docker-compose up
```
(Note: If you wish to run the containers in detached mode such that the logs are not displayed in the terminal, you may do so by addding the "-d" flag to "docker-compose up". e.g., "docker-compose up -d".)

The API should be available at http://localhost and the CQF Ruler server should be available at http://localhost:8080/cqf-ruler-r4.

### Advanced Configuration
Port Configuration

CQF Ruler Database Configuration

## All of the below information is out of date and needs to be changed
## Installing / Getting started

Prerequisites to install:
- Docker Desktop 3.2.2
- Brew (for MacOS)
- Python 3
- pip 3
- git
- bash/zsh terminal (recommend Iterm for MacOS)
- VS Code (recommended IDE)


## Developing

In order to run the code and test changes, you'll need to clone the code from Enterprise GitHub and set up the Docker containers:

```shell
git clone https://github.gatech.edu/HDAP/SmartPacer-RC-API.git
```
Pull the `main` branch (this is the project main working/dev branch).
