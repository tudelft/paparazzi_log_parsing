function plot_powers(ac_data, filter)
    if ~isfield(ac_data, 'POWER_DEVICE')
        return
    end
    
    % Default is no filtering
    if nargin < 2
        filter =  0
    end

    [b, a] = butter(2,0.1);
    
    ac_data.POWER_DEVICE.id = double(ac_data.POWER_DEVICE.node_id) + double(ac_data.POWER_DEVICE.circuit) ./ 100;
    pwr = unique(ac_data.POWER_DEVICE.id)

    hold off
    leg = []
    for i=1:size(pwr,1)
        p=pwr(i);
        id = find(ac_data.POWER_DEVICE.id == p);
        x = ac_data.POWER_DEVICE.current(id) .* ac_data.POWER_DEVICE.voltage(id);
        filter_leg = '';
        if filter
            plot(filtfilt(b,a,x),'linewidth',2)
            filter_leg = ' filtered';
        else
            plot(x)
        end
        leg =  [leg, num2str(p)+filter];
        hold on
    end
        
    grid on
    legend(strcat('Node ',string(pwr)));
