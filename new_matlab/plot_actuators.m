function plot_actuators(ac_data, idx)
    % Plot the ACTUATORS message
    if isfield(ac_data, 'ACTUATORS')
        if ~exist('idx','var')
            idx = [1:size(ac_data.ACTUATORS.values,2)];
        end
            
        act_values = double(string(ac_data.ACTUATORS.values)); 
        plot(ac_data.ACTUATORS.timestamp, act_values(:,idx));
        
        title('Aactuator values');
        xlabel('Time [s]');
        legend(strcat('ACT',string(idx), '[pprz]'));
    end
end