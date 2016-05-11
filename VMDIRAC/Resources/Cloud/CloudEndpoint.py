###########################################################
# $HeadURL$
# File: CloudEndpoint.py
# Original author: Victor Mendez
# Modified: A.T.
###########################################################

"""
   CloudEndpoint is a base class for the clients used to connect to different
   cloud providers
"""

__RCSID__ = '$Id$'

import os
import time
import ssl
import sys

from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from libcloud                   import security
from libcloud.compute.types     import Provider
from libcloud.compute.providers import get_driver

# DIRAC
from DIRAC import gLogger, gConfig, S_OK, S_ERROR
from DIRAC.Core.Utilities.File import makeGuid
from DIRAC.ConfigurationSystem.Client.Helpers.Operations import Operations

def createMimeData( userDataTuple ):

  userData = MIMEMultipart()
  for contents, mtype, fname in userDataTuple:
    try:
      mimeText = MIMEText(contents, mtype, sys.getdefaultencoding())
      mimeText.add_header('Content-Disposition', 'attachment; filename="%s"' % fname )
      userData.attach( mimeText )
    except Exception as e:
      return S_ERROR( str( e ) )

  return S_OK( userData )

class CloudEndpoint( object ):
  """ CloudEndpoint base class
  """

  def __init__( self, parameters = {} ):
    """
    """
    # logger
    self.log = gLogger.getSubLogger( 'CloudEndpoint' )
    self.parameters = parameters
    self.valid = False
    result = self.initialize()
    if result['OK']:
      self.valid = True

  def isValid( self ):
    return self.valid

  def setParameters( self, parameters ):
    self.parameters = parameters

  def getParameterDict( self ):
    return self.parameters

  def initialize( self ):

    # Relax security
    security.SSL_VERSION = ssl.PROTOCOL_SSLv23
    security.VERIFY_SSL_CERT = False

    # Variables needed to contact the service
    connDict = {}
    for var in [ 'ex_force_auth_url', 'ex_force_service_region', 'ex_force_auth_version',
                 'ex_tenant_name', 'ex_keyname', 'ex_voms_proxy']:
      if var in self.parameters:
        connDict[var] = self.parameters[var]

    username = self.parameters.get( 'User' )
    password = self.parameters.get( 'Password' )

    # log info:
    os.system("export LIBCLOUD_DEBUG=/tmp/libcloud.log")
    for key in connDict:
      self.log.info( "%s: %s" % ( key, connDict[key] ) )

    # get openstack driver
    providerName = self.parameters.get( 'CEType', 'OPENSTACK' ).upper()
    providerCode = getattr( Provider, providerName )
    self.driverClass = get_driver( providerCode )

    self.__driver = self.driverClass( username, password, **connDict )

    result = self.__checkConnection()
    return result

  def __checkConnection( self ):
    """
    Checks connection status by trying to list the images.

    :return: S_OK | S_ERROR
    """
    try:
      _result = self.__driver.list_images()
      # the libcloud library, throws Exception. Nothing to do.
    except Exception, errmsg:
      return S_ERROR( errmsg )

    return S_OK()

  def __getImageByName( self, imageName ):
    """
    Given the imageName, returns the current image object from the server.

    :Parameters:
      **imageName** - `string`
        imageName as stored on the OpenStack image repository ( glance )

    :return: S_OK( image ) | S_ERROR
    """
    try:
      images = self.__driver.list_images()
      # the libcloud library, throws Exception. Nothing to do.
    except Exception as errmsg:
      return S_ERROR( errmsg )

    image = None
    for im in images:
      if im.name == imageName:
        image = im
        break

    if image is None:
       return S_ERROR( "Image %s not found" % imageName )

    return S_OK( image )

  def __getFlavorByName( self, flavorName ):
    """
    Given the flavorName, returns the current flavor object from the server.

    :Parameters:
      **flavorName** - `string`
        flavorName as stored on the OpenStack service

    :return: S_OK( flavor ) | S_ERROR
    """
    try:
      flavors = self.__driver.list_sizes()
      # the libcloud library, throws Exception. Nothing to do.
    except Exception as errmsg:
      return S_ERROR( errmsg )

    flavor = None
    for fl in flavors:
      if fl.name == flavorName:
        flavor = fl

    if flavor is None:
      return S_ERROR( "Flavor %s not found" % flavorName )

    return S_OK( flavor )

  def __getSecurityGroups( self, securityGroupNames = [] ):
    """
    Given the securityGroupName, returns the current security group object from the server.

    :Parameters:
      **securityGroupName** - `string`
        securityGroupName as stored on the OpenStack service

    :return: S_OK( securityGroup ) | S_ERROR
    """

    if not securityGroupNames:
      securityGroupNames = []
    elif not isinstance( securityGroupNames, list ):
      securityGroupNames = [ securityGroupNames ]

    if not 'default' in securityGroupNames:
      securityGroupNames.append( 'default' )

    try:
      secGroups = self.__driver.ex_list_security_groups()
      # the libcloud library, throws Exception. Nothing to do.
    except Exception as errmsg:
      return S_ERROR( errmsg )

    return S_OK( [ secGroup for secGroup in secGroups if secGroup.name in securityGroupNames ] )

  def __createUserDataScript( self ):

    userDataDict = {}

    # Arguments to the vm-bootstrap command
    bootstrapArgs = { 'dirac-site': self.parameters['Site'],
                      'submit-pool': self.parameters['SubmitPool'],
                      'ce-name': self.parameters['CEName'],
                      'image-name': self.parameters['Image'],
                      'vm-uuid': self.parameters['VMUUID'],
                      'vmtype': self.parameters['VMType'],
                      'vo': self.parameters['VO'],
                      'running-pod': self.parameters['RunningPod'],
                      'cvmfs-proxy': self.parameters.get( 'CVMFSProxy', 'None' ),
                      'cs-servers': ','.join( self.parameters.get( 'CSServers', [] ) ),
                      'release-version': self.parameters['Version'] ,
                      'release-project': self.parameters['Project'] ,
                      'setup': self.parameters['Setup'] }

    bootstrapString = ''
    for key, value in bootstrapArgs.items():
      bootstrapString += " --%s=%s \\\n" % ( key, value )
    userDataDict['bootstrapArgs'] = bootstrapString

    userDataDict['user_data_commands_base_url'] = self.parameters.get( 'user_data_commands_base_url' )
    if not userDataDict['user_data_commands_base_url']:
      return S_ERROR( 'user_data_commands_base_url is not defined' )
    with open( self.parameters['HostCert'] ) as cfile:
      userDataDict['user_data_file_hostkey'] = cfile.read().strip()
    with open( self.parameters['HostKey'] ) as kfile:
      userDataDict['user_data_file_hostcert'] = kfile.read().strip()

    # List of commands to be downloaded
    bootstrapCommands = self.parameters.get( 'user_data_commands' )
    if isinstance( bootstrapCommands, basestring ):
      bootstrapCommands = bootstrapCommands.split( ',' )
    if not bootstrapCommands:
      return S_ERROR( 'user_data_commands list is not defined' )
    userDataDict['bootstrapCommands'] = ' '.join( bootstrapCommands )

    script = """
cat <<X5_EOF >/root/hostkey.pem
%(user_data_file_hostkey)s
%(user_data_file_hostcert)s
X5_EOF
mkdir -p /var/spool/checkout/context
cd /var/spool/checkout/context
for dfile in %(bootstrapCommands)s
do
  echo curl --insecure -s %(user_data_commands_base_url)s/$dfile -o $dfile
  i=7
  while [ $i -eq 7 ]
  do
    curl --insecure -s %(user_data_commands_base_url)s/$dfile -o $dfile
    i=$?
    if [ $i -eq 7 ]; then
      echo curl connection failure for file $dfile
      sleep 10
    fi
  done
  curl --insecure -s %(user_data_commands_base_url)s/$dfile -o $dfile || echo Download of $dfile failed with $? !
done
chmod +x vm-bootstrap
/var/spool/checkout/context/vm-bootstrap %(bootstrapArgs)s
#/sbin/shutdown -h now
    """ % userDataDict

    if "HEPIX" in self.parameters:
      script = """
cat <<EP_EOF >>/var/lib/hepix/context/epilog.sh
#!/bin/sh
%s
EP_EOF
chmod +x /var/lib/hepix/context/epilog.sh
      """ % script

    user_data = """#!/bin/bash
mkdir -p /etc/joboutputs
(
%s
) > /etc/joboutputs/user_data.log 2>&1 &
exit 0
    """ % script

    cloud_config = """#cloud-config

output: {all: '| tee -a /var/log/cloud-init-output.log'}

cloud_final_modules:
  - scripts-user
    """

    return createMimeData( ( ( user_data, 'text/x-shellscript', 'dirac_boot.sh' ),
                             ( cloud_config, 'text/cloud-config', 'cloud-config') ) )

  def createInstances( self, vmsToSubmit ):
    outputDict = {}

    for nvm in xrange( vmsToSubmit ):
      instanceID = makeGuid()[:8]
      result = self.createInstance( instanceID )
      if result['OK']:
        node, _publicIP = result['Value']
        outputDict[node.id] = instanceID
      else:
        break

    return S_OK( outputDict )

  def createInstance( self, instanceID = '' ):
    """
    This creates a VM instance for the given boot image
    and creates a context script, taken the given parameters.
    Successful creation returns instance VM

    Boots a new node on the OpenStack server defined by self.endpointConfig. The
    'personality' of the node is done by self.imageConfig. Both variables are
    defined on initialization phase.

    The node name has the following format:
    <bootImageName><contextMethod><time>

    It boots the node. If IPpool is defined on the imageConfiguration, a floating
    IP is created and assigned to the node.

    :return: S_OK( ( nodeID, publicIP ) ) | S_ERROR
    """

    if not instanceID:
      instanceID = makeGuid()[:8]

    self.parameters['VMUUID'] = instanceID
    self.parameters['VMType'] = self.parameters.get( 'CEType', 'OpenStack' )

    createNodeDict = {}

    # Get the image object
    if not "ImageID" in self.parameters and 'ImageName' in self.parameters:
      result = self.__getImageByName( self.parameters['ImageName'] )
      if not result['OK']:
        return result
      image = result['Value']
    elif "ImageID" in self.parameters:
      try:
        image = self.__driver.get_image( self.parameters['ImageID'] )
      except Exception as e:
        if "Image not found" in str( e ):
          return S_ERROR( "Image with ID %s not found" % self.parameters['ImageID'] )
        else:
          return S_ERROR( "Failed to get image for ID %s" % self.parameters['ImageID'] )
    else:
      return S_ERROR( 'No image specified' )
    createNodeDict['image'] = image

    # Get the flavor object
    if not "FlavorID" in self.parameters and 'FlavorName' in self.parameters:
      result = self.__getFlavorByName( self.parameters['FlavorName'] )
      if not result['OK']:
        return result
      flavor = result['Value']
    elif 'FlavorID' in self.parameters:
      flavor = self.__driver.ex_get_size( self.parameters['FlavorID'] )
    else:
      return S_ERROR( 'No flavor specified' )
    createNodeDict['size'] = flavor

    # Get security groups
    #if 'ex_security_groups' in self.parameters:
    #  result = self.__getSecurityGroups( self.parameters['ex_security_groups'] )
    #  if not result[ 'OK' ]:
    #    self.log.error( result[ 'Message' ] )
    #    return result
    #  self.parameters['ex_security_groups'] = result[ 'Value' ]

    result = self.__createUserDataScript()
    if not result['OK']:
      return result

    createNodeDict['ex_userdata'] = result['Value']

    # Optional node contextualization parameters
    for param in [ 'ex_metadata', 'ex_pubkey_path', 'ex_keyname' ]:
      if param in self.parameters:
        createNodeDict[param] = self.parameters[param]

    createNodeDict['name'] = 'DIRAC_%s' % instanceID

    createNodeDict['ex_config_drive'] = True

    self.log.info( "Creating node:" )
    for key, value in createNodeDict.items():
      self.log.verbose( "%s: %s" % ( key, value ) )

    if 'networks' in self.parameters:
      result = self.getVMNetwork()
      if not result['OK']:
        return result
      createNodeDict['networks'] = result['Value']
    if 'keyname' in self.parameters:
      createNodeDict['ex_keyname'] = self.parameters['keyname']

    # Create the VM instance now
    try:
      vmNode = self.__driver.create_node( **createNodeDict )

    except Exception as errmsg:
      gLogger.debug( "Exception in driver.create_node", errmsg )
      return S_ERROR( errmsg )

    publicIP = None
    if "CreatePublicIP" in self.parameters:

      # Wait until the node is running, otherwise getting public IP fails
      try:
        self.__driver.wait_until_running( [vmNode], timeout = 60 )
        result = self.assignFloatingIP( vmNode )
        if result['OK']:
          publicIP = result['Value']
      except Exception as exc:
        return S_ERROR( 'Failed to wait until the node is Running' )

    return S_OK( ( vmNode, publicIP ) )

  def getVMNode( self, nodeID ):
    """
    Given a Node ID, returns all its configuration details on a
    libcloud.compute.base.Node object.

    :Parameters:
      **nodeID** - `string`
        openstack node id ( not uuid ! )

    :return: S_OK( Node ) | S_ERROR
    """

    try:
      node = self.__driver.ex_get_node_details( nodeID )
    except Exception, errmsg:
      return S_ERROR( errmsg )

    return S_OK( node )

  def getVMStatus( self, nodeID ):
    """
    Get the status for a given node ID. libcloud translates the status into a digit
    from 0 to 4 using a many-to-one relation ( ACTIVE and RUNNING -> 0 ), which
    means we cannot undo that translation. It uses an intermediate states mapping
    dictionary, SITEMAP, which we use here inverted to return the status as a
    meaningful string. The five possible states are ( ordered from 0 to 4 ):
    RUNNING, REBOOTING, TERMINATED, PENDING & UNKNOWN.

    :Parameters:
      **uniqueId** - `string`
        openstack node id ( not uuid ! )

    :return: S_OK( status ) | S_ERROR
    """

    result = self.getVMNode( nodeID )
    if not result[ 'OK' ]:
      return result

    state = result[ 'Value' ].state

    # reversed from libcloud
    stateMapDict = { 0 : 'RUNNING',
                     1 : 'REBOOTING',
                     2 : 'TERMINATED',
                     3 : 'PENDING',
                     4 : 'UNKNOWN' }

    if not state in stateMapDict:
      return S_ERROR( 'State %s not in STATEMAP' % state )

    return S_OK( stateMapDict[ state ] )

  def getVMNetwork( self, networkNames = [] ):
    """ Get a network object corresponding to the networkName

    :param str networkName: network name
    :return: S_OK|S_ERROR network object in case of S_OK
    """
    resultList = []
    nameList = list( networkNames )
    if not nameList:
      nameList = self.parameters.get( 'networks' )
      if not nameList:
        return S_ERROR( 'Network names are not specified' )
      else:
        nameList = nameList.split( ',' )

    result = self.__driver.ex_list_networks()
    for oNetwork in result:
      if oNetwork.name in nameList:
        resultList.append( oNetwork )

    return S_OK( resultList )

  def stopVM( self, nodeID, publicIP = '' ):
    """
    Given the node ID it gets the node details, which are used to destroy the
    node making use of the libcloud.openstack driver. If three is any public IP
    ( floating IP ) assigned, frees it as well.

    :Parameters:
      **uniqueId** - `string`
        openstack node id ( not uuid ! )
      **public_ip** - `string`
        public IP assigned to the node if any

    :return: S_OK | S_ERROR
    """

    # Get Node object with node details
    result = self.getVMNode( nodeID )
    if not result[ 'OK' ]:
      return result
    node = result[ 'Value' ]

    # Delete floating IP if any
    if publicIP:
      result = self.deleteFloatingIP( publicIP, node )
      if not result['OK']:
        self.log.error( 'Failed in deleteFloatingIP:', result[ 'Message' ] )
        return result

    # Destroy the VM instance
    try:
      result = self.__driver.destroy_node( node )
      if not result:
        return S_ERROR( "Failed to destroy node: %s" % node.id )
    except Exception as errmsg:
      return S_ERROR( errmsg )

    return S_OK()

  def getVMPool( self, poolName ):

    try:
      poolList = self.__driver.ex_list_floating_ip_pools()
      for pool in poolList:
        if pool.name == poolName:
          return S_OK( pool )
    except Exception as errmsg:
      return S_ERROR( errmsg )

    return S_ERROR( 'IP Pool with the name %s not found' % poolName )

  #.............................................................................
  # Private methods

  def assignFloatingIP( self, node ):
    """
    Given a node, assign a floating IP from the ipPool defined on the imageConfiguration
    on the CS.

    :Parameters:
      **node** - `libcloud.compute.base.Node`
        node object with the vm details

    :return: S_OK( public_ip ) | S_ERROR
    """

    ipPool = self.parameters.get( 'ipPool' )

    if ipPool:
      try:
        poolList = self.__driver.ex_list_floating_ip_pools()
        for pool in poolList:
          if pool.name == ipPool:
            floatingIP = pool.create_floating_ip()
            self.__driver.ex_attach_floating_ip_to_node( node, floatingIP )
            publicIP = floatingIP.ip_address
            return S_OK( publicIP )
        return S_ERROR( 'ipPool=%s is not defined in the openstack endpoint' % ipPool )

      except Exception as errmsg:
        return S_ERROR( errmsg )

      return S_ERROR( errmsg )

    # for the case of not using floating ip assignment
    return S_OK( '' )

  def deleteFloatingIP( self, publicIP, node ):
    """
    Deletes a floating IP <public_ip> from the server.

    :Parameters:
      **public_ip** - `string`
        public IP to be deleted

    :return: S_OK | S_ERROR
    """

    # We are still with IPv4
    publicIP = publicIP.replace( '::ffff:', '' )

    ipPool = self.parameters.get( 'ipPool' )

    if ipPool:
      try:
        poolList = self.__driver.ex_list_floating_ip_pools()
        for pool in poolList:
          if pool.name == ipPool:
            floatingIP = pool.get_floating_ip( publicIP )
            self.__driver.ex_detach_floating_ip_from_node( node, floatingIP )
            floatingIP.delete()
            return S_OK()

        return S_ERROR( 'ipPool=%s is not defined in the openstack endpoint' % ipPool )

      except Exception as errmsg:
        return S_ERROR( errmsg )

    return S_OK()
