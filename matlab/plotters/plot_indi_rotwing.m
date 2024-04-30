function plot_indi_rotwing(ac_data)
    if ~isfield(ac_data, 'STAB_ATTITUDE')
        return
    end
    %plot(ac_data.STAB_ATTITUDE_INDI.timestamp, [ac_data.STAB_ATTITUDE_INDI.u(:,1), ac_data.STAB_ATTITUDE_INDI.u(:,2), ac_data.STAB_ATTITUDE_INDI.u(:,3), ac_data.STAB_ATTITUDE_INDI.u(:,4)]);
    ax1 = subplot(2,1,1);
    act_values = double(string(ac_data.STAB_ATTITUDE.u)); 
    plot(ac_data.STAB_ATTITUDE.timestamp, act_values(:,1));
    hold on;
    plot(ac_data.STAB_ATTITUDE.timestamp, act_values(:,2));
    hold on;
    plot(ac_data.STAB_ATTITUDE.timestamp, act_values(:,3));
    hold on;
    plot(ac_data.STAB_ATTITUDE.timestamp, act_values(:,4));
    hold on;
    
    plot(ac_data.STAB_ATTITUDE.timestamp, act_values(:,5));
    hold on;
    plot(ac_data.STAB_ATTITUDE.timestamp, act_values(:,6));
    hold on;
    plot(ac_data.STAB_ATTITUDE.timestamp, act_values(:,7));
    hold on;
    plot(ac_data.STAB_ATTITUDE.timestamp, act_values(:,8));
    hold on;
    plot(ac_data.STAB_ATTITUDE.timestamp, act_values(:,9));
    hold on;

    title('Indi_u');
    xlabel('Time [s]');
    legend('front', 'right', 'back', 'left', 'rudder', 'elevator', 'aileron', 'flaps', 'pusher');

    ax2 = subplot(2,1,2);
    plot(ac_data.STAB_ATTITUDE.timestamp, (act_values(:,1) + act_values(:,2) + act_values(:,3) + act_values(:,4)) .* -0.000575);

    title('Specific thrust');
    xlabel('Time [s]');
    legend('specific_thrust');

    sgtitle('indi u')
    linkaxes([ax1,ax2],'x')

end
