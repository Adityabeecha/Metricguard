import type{Result}from './api';

export function Shapley({value}:{value:Result['shapley']}){
 if(!value)return null;
 return <section className="panel"><div className="panel-header"><h2>Exact three-rule Shapley allocation</h2><p>Optional average over all six rule orders. The ordered bridge above remains the primary sequence.</p></div>{value.status==='complete'?<div className="table-scroll"><table><thead><tr><th>RULE</th><th>DISPLAY INR</th><th>EXACT FRACTION</th></tr></thead><tbody>{value.contributions.map(item=><tr key={item.rule}><td className="weight">{item.rule.replaceAll('_',' ')}</td><td className="mono-cell">{item.display_decimal}</td><td className="mono-cell">{item.numerator}/{item.denominator}</td></tr>)}</tbody></table></div>:<p className="panel-note">Allocation unavailable because a required hybrid endpoint is undefined or unsafe to evaluate.</p>}<p className="panel-note">{value.limitation}</p></section>;
}
