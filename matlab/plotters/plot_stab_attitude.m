function plot_stab_attitude(ac_data, order)
    if ~isfield(ac_data, 'STAB_ATTITUDE')
        return
    end

    ax1 = subplot(3,1,1);
    hold on
    plot(ac_data.STAB_ATTITUDE.timestamp, ac_data.STAB_ATTITUDE.angular_accel_alt(:,1), 'color', '#0072BD');
    plot(ac_data.STAB_ATTITUDE.timestamp, ac_data.STAB_ATTITUDE.angular_accel_ref_alt(:,1), '--', 'color', '#0072BD');
    plot(ac_data.STAB_ATTITUDE.timestamp, ac_data.STAB_ATTITUDE.angular_accel_alt(:,2), 'color', '#D95319');
    plot(ac_data.STAB_ATTITUDE.timestamp, ac_data.STAB_ATTITUDE.angular_accel_ref_alt(:,2), '--', 'color', '#D95319');
    plot(ac_data.STAB_ATTITUDE.timestamp, ac_data.STAB_ATTITUDE.angular_accel_alt(:,3), 'color', '#EDB120');
    plot(ac_data.STAB_ATTITUDE.timestamp, ac_data.STAB_ATTITUDE.angular_accel_ref_alt(:,3), '--', 'color', '#EDB120');
    title('angular acceleration'); xlabel('time(s)'); ylabel('(deg/s^2)'); legend('pdot','pdotref','qdot','qdotref','rdot','rdotref')

    ax2 = subplot(3,1,2);
    hold on
    plot(ac_data.STAB_ATTITUDE.timestamp, ac_data.STAB_ATTITUDE.angular_rate_alt(:,1), 'color', '#0072BD');
    plot(ac_data.STAB_ATTITUDE.timestamp, ac_data.STAB_ATTITUDE.angular_rate_ref_alt(:,1), '--', 'color', '#0072BD');
    plot(ac_data.STAB_ATTITUDE.timestamp, ac_data.STAB_ATTITUDE.angular_rate_alt(:,2), 'color', '#D95319');
    plot(ac_data.STAB_ATTITUDE.timestamp, ac_data.STAB_ATTITUDE.angular_rate_ref_alt(:,2), '--', 'color', '#D95319');
    plot(ac_data.STAB_ATTITUDE.timestamp, ac_data.STAB_ATTITUDE.angular_rate_alt(:,3), 'color', '#EDB120');
    plot(ac_data.STAB_ATTITUDE.timestamp, ac_data.STAB_ATTITUDE.angular_rate_ref_alt(:,3), '--', 'color', '#EDB120');
    title('angular rates'); xlabel('time(s)'); ylabel('(deg/s)'); legend('p','pref','q','qref','r','rref')

    ax3 = subplot(3,1,3);
    hold on
    plot(ac_data.STAB_ATTITUDE.timestamp, ac_data.STAB_ATTITUDE.att_alt(:,1), 'color', '#0072BD');
    plot(ac_data.STAB_ATTITUDE.timestamp, ac_data.STAB_ATTITUDE.att_ref_alt(:,1), '--', 'color', '#0072BD');
    plot(ac_data.STAB_ATTITUDE.timestamp, ac_data.STAB_ATTITUDE.att_alt(:,2), 'color', '#D95319');
    plot(ac_data.STAB_ATTITUDE.timestamp, ac_data.STAB_ATTITUDE.att_ref_alt(:,2), '--', 'color', '#D95319');
    plot(ac_data.STAB_ATTITUDE.timestamp, ac_data.STAB_ATTITUDE.att_alt(:,3), 'color', '#EDB120');
    plot(ac_data.STAB_ATTITUDE.timestamp, ac_data.STAB_ATTITUDE.att_ref_alt(:,3), '--', 'color', '#EDB120');
    title('Euler angles'); xlabel('time(s)'); ylabel('(deg)'); legend('phi','phiref','theta','thetaref','psi','psiref')

    sgtitle(['Stab attitude'])
    linkaxes([ax1,ax2,ax3],'x')
end