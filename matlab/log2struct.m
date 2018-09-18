function s = log2struct(logfilename)
% parse a PPRZ log file
%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
% function s = log2struct(logfilename)
% Parses a log data file from the Paparazzi ground station into a 
% Matlab variable. The log is sorted by aircraft ID and message ID.
% Parsing the log definition file (xx.log) is not required.
% 
% Argument
% The name of the log data file to be parsed.
% May be omitted, in which case a file open dialog is used.
% 
% Returns
% A struct of dimension 1 x nAircraft
% For each type of message received there is a substruct with the fields
% timestamp and contents as arrays over time. The columns of the contents
% array correspond to the message's data fields as defined in messages.xml.
%
% Example
% >> % 3D plot of the trajectory of aircraft #1
% >> s = log2struct('09_08_26__14_18_29.data');
% >> plot3(s(1).GPS.content(:,2), s(1).GPS.content(:,3),s(1).GPS.content(:,5))
% >> % what did the servos do?
% >> plot (s(1).ACTUATORS.timestamp, s(1).ACTUATORS.content)
% 
% Known Bugs
% The method fails when the number of data elements for one message type
% changes during the log file, as may happen when the configuration file is
% changed in between or with arrays of variable size.
% 
%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
%
% Author: Andreas Gaeb
% Created:  27-Aug-2009
% $Id$
%
% Copyright (C) 2009 Andreas Gaeb
%
% This file is part of paparazzi.
%
% paparazzi is free software; you can redistribute it and/or modify
% it under the terms of the GNU General Public License as published by
% the Free Software Foundation; either version 2, or (at your option)
% any later version.
%
% paparazzi is distributed in the hope that it will be useful,
% but WITHOUT ANY WARRANTY; without even the implied warranty of
% MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
% GNU General Public License for more details.
%
% You should have received a copy of the GNU General Public License
% along with paparazzi; see the file COPYING.  If not, write to
% the Free Software Foundation, 59 Temple Place - Suite 330,
% Boston, MA 02111-1307, USA.
%
%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%

persistent log2struct_old_working_dir

% no arguments? open dialog
if nargin < 1
  [filename, pathname] = uigetfile(...
    {'*.data', 'Paparazzi log data files (*.data)'}, ...
    'Open data file', ...
    log2struct_old_working_dir);
  if ~filename, return, end
        logfilename = fullfile(pathname, filename);
  log2struct_old_working_dir = pathname; 
  % display the filename for copy/paste
  disp (['parsing ' logfilename '...']);
end

fid = fopen(logfilename);
if fid == -1
  error('File %s not found', logfilename);
end

try
  % read everything as timestamp, A/C ID, msg name and msg contents
  C = textscan(fid, '%f %u %s %[^\n]');
  
  if isempty(C{1})
    error ('File %s does not seem to be a Paparazzi log data file.', 
logfilename);
  end
  
  timestamp = C{1};
  aircraftID = C{2};
  msgName = C{3};
  msgContent = C{4};
  
  % find s which messages have been sent
  uniqueMsgs = unique(msgName);
  nMsg = size(uniqueMsgs,1);
  
  % find s which aircraft sent something
  uniqueAC = unique(aircraftID);
  nAC = size(uniqueAC,1);
  
  % distribute the messages to their sending aircraft
  for iAC = nAC:-1:1 % counting backwards eliminates preallocation
    % the ID of ith aircraft
    acID = uniqueAC(iAC);
    s(iAC).AIRCRAFT_ID = acID;
    
    % the indices of all messages send by ith aircraft 
    allMsgsFromThisAircraft = (aircraftID == uniqueAC(iAC)); 
    timestampFromThisAircraft = timestamp(allMsgsFromThisAircraft);
    msgNameFromThisAircraft = msgName(allMsgsFromThisAircraft);
    msgContentFromThisAircraft = msgContent(allMsgsFromThisAircraft);
    
    % for each message name
    for iMsg = 1:nMsg
      theMsgName = uniqueMsgs{iMsg};
      
      % when was this special message sent by the ith aircraft?
      thisMsgFromThisAircraft = strmatch(...
        theMsgName, msgNameFromThisAircraft, 'exact');
      
      % record with timestamp and contents
      s(iAC).(theMsgName).timestamp = ...
        timestampFromThisAircraft(thisMsgFromThisAircraft);
      [s(iAC).(theMsgName).content, status] = str2num(char(...
        msgContentFromThisAircraft(thisMsgFromThisAircraft))); %#ok<ST2NM>
      if ~status && ~strcmp(theMsgName, 'PONG')
        warning('Paparazzi:log2struct:EmptyContent', ...
          'Could not read contents of %s message from aircraft #%i', ...
          theMsgName, acID);
        continue;
      end
    end  
    
  end
  
  fclose (fid);
  
catch theError
  fclose(fid);
  rethrow(theError);
end
