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

### Git LFS Troubleshooting
The CQF Ruler R4 war file is stored using Git Large File System. While the command to recursively pull submodules should download the full file properly, some users have noted unexpected behavior. If any issue is encountered, it is recommended that you follow your operating system specific instructions on installing Git LFS. For example, on a MAC using Brew you may run:
```
brew install git-lfs
```
Once you have Git LFS fully installed, navigate into the CQF Ruler sub module directory within the project directory. Once there, attempt to run:
```
git lfs fetch
```
This should appropriately update all files with the LFS complete version.

### Advanced Configuration
#### CQF Ruler Port Configuration
Please be aware that as of the current version of this release, the CQF Ruler image as provided by the developers doesn't allow for simple handling of port configuration. The external host port mapping to the internal Jetty port is unreliable and only functions if the ports match. This means that the external host port must be set to 8080 to match the default Jetty port, unless you wish to modify the CQF Ruler build scripts to add the proper flag.

Future versions of RC API will include a further modified version of the CQF Ruler image to allow for more additional customization through the `docker-compose.yml`.

#### CQF Ruler Database Configuration
CQF Ruler leverages HAPI FHIR as its FHIR Server, adding additional functionality on top of it. As such, it allows for multiple options configuring the underlying database in accordance with the standard HAPI FHIR server. While a brief rundown will be given here, for full information, it is recommended that you look at the official HAPI FHIR documentation which may be found at the following links:
* [https://hapifhir.io/hapi-fhir/docs/]
* [https://github.com/hapifhir/hapi-fhir-jpaserver-starter]

This project's default configuration uses the container internal H2 database option. To modify this, you will need to open the `hapi.properties` file in the `/cqf-ruler/r4/src/main/resources/` folder. Database settings begin on line 70. You should comment out the first section referencing H2 (lines 73-77) and uncomment the lines below appropriate to the database configuration you wish to use.

This project's `docker-compose.yml` includes a sample entry for adding a Postgres database, as well as commented out lines under the CQF Ruler entry that will set the CQF Ruler container to have a dependency on that database for deployment purposes. Please note that the Postgres entry is provided as an example only and should not be expected to be fully functional out of the box.
